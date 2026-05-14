"""Opt-in live tests for individual agent nodes.

These tests intentionally avoid the full pipeline. They forge the minimum
upstream state needed for the Solver and Validator nodes, then run the live
LLM call while stubbing the expensive/non-essential dependency for that node:

- Solver: stub RAG retrieval so no Postgres or Gemini embedding call is needed.
- Validator: stub the Docker sandbox so we can still exercise the validator
  logic and live review without requiring Docker.

Enable with:

    TOROIDBOT_LIVE_AGENT_TESTS=1 pytest tests/test_live_agents.py -v

You can provide comma-separated model fallbacks with:

    TOROIDBOT_LIVE_MODEL_CANDIDATES=google-gla:gemini-2.5-flash,google-gla:gemini-2.5-flash-lite

or agent-specific variants:

    TOROIDBOT_LIVE_SOLVER_MODEL_CANDIDATES=...
    TOROIDBOT_LIVE_VALIDATOR_MODEL_CANDIDATES=...
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

import pytest

from agents.event_config import EventConfig
from agents.schemas import (
    Category,
    ChallengeCode,
    ChallengeInfra,
    ChallengeManifest,
    ChallengeSolver,
    ChallengeStory,
    CTFState,
    RetryTarget,
    ValidationCheck,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TOROIDBOT_LIVE_AGENT_TESTS") != "1",
    reason="live agent tests are opt-in; set TOROIDBOT_LIVE_AGENT_TESTS=1 to run them",
)

EXPECTED_FLAG = "CTF{solver-validator-live-2026}"


VULNERABLE_APP_PY = (
    "from flask import Flask, request, render_template_string\n"
    "import sqlite3\n"
    "from pathlib import Path\n"
    "import secrets\n"
    "app = Flask(__name__)\n"
    "DB = Path('/tmp/live-agent-test.db')\n"
    "LOGIN_PAGE = '''<form method=post>\n"
    "<input name=user> <input name=pass> <button>Login</button>\n"
    "</form>{{message}}'''\n"
    "def init_db():\n"
    "    conn = sqlite3.connect(DB)\n"
    "    cur = conn.cursor()\n"
    "    cur.execute('CREATE TABLE IF NOT EXISTS users(name TEXT, pass TEXT)')\n"
    "    cur.execute('DELETE FROM users')\n"
    "    cur.execute('INSERT INTO users VALUES(?, ?)', ('admin', secrets.token_hex(8)))\n"
    "    conn.commit()\n"
    "    conn.close()\n"
    "def lookup_user(user, password):\n"
    "    conn = sqlite3.connect(DB)\n"
    "    cur = conn.cursor()\n"
    "    cur.execute(\"SELECT * FROM users WHERE name='\" + user + \"' AND pass='\" + password + \"'\")\n"
    "    return cur.fetchone()\n"
    "@app.route('/', methods=['GET', 'POST'])\n"
    "def login():\n"
    "    message = ''\n"
    "    if request.method == 'POST':\n"
    "        row = lookup_user(request.form['user'], request.form['pass'])\n"
    "        if row:\n"
    "            message = 'FLAG: ' + open('/flag.txt').read().strip()\n"
    "        else:\n"
    "            message = 'invalid'\n"
    "    return render_template_string(LOGIN_PAGE, message=message)\n"
    "init_db()\n"
)


def _model_candidates(agent: str) -> list[str]:
    specific = os.getenv(f"TOROIDBOT_LIVE_{agent.upper()}_MODEL_CANDIDATES")
    generic = os.getenv("TOROIDBOT_LIVE_MODEL_CANDIDATES")
    raw = specific or generic or os.getenv("TOROIDBOT_LIVE_MODEL") or "google-gla:gemini-2.5-flash"
    return [candidate.strip() for candidate in raw.split(",") if candidate.strip()]


def _looks_retryable(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    retryable_markers = (
        "429",
        "rate limit",
        "quota",
        "resource_exhausted",
        "overloaded",
        "temporarily unavailable",
        "service unavailable",
        "timeout",
        "connection reset",
        "unexpected model",
        "invalid response",
        "validation error",
        "output validation",
    )
    return any(marker in message for marker in retryable_markers)


def _mirror_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """pydantic-ai's Google provider checks GOOGLE_API_KEY, while this repo
    documents GEMINI_API_KEY. Mirror the value so either env var works.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and not os.getenv("GOOGLE_API_KEY"):
        monkeypatch.setenv("GOOGLE_API_KEY", gemini_key)


def _import_live_node(module_name: str):
    """Import a live agent node or skip with a useful message.

    If the user accidentally runs the system pytest instead of the project venv,
    the live agent imports fail because pydantic_ai is not available there.
    Skip rather than crashing so the failure mode is explicit.
    """
    try:
        module = __import__(module_name, fromlist=["*"])
    except ModuleNotFoundError as exc:
        if exc.name == "pydantic_ai":
            pytest.skip(
                "pydantic_ai is not installed in the active interpreter. "
                "Run the tests with the project venv, e.g. `source .venv/bin/activate.csh` "
                "followed by `python3 -m pytest tests/test_live_agents.py -v`.",
                allow_module_level=False,
            )
        raise
    return module


async def _run_with_fallbacks(state: CTFState, run_fn, agent: str) -> CTFState:
    last_error: Exception | None = None
    for model in _model_candidates(agent):
        trial_state = state.model_copy(deep=True)
        trial_state.model = model
        try:
            return await run_fn(trial_state)
        except Exception as exc:  # pragma: no cover - exercised when a provider/model fails
            last_error = exc
            if not _looks_retryable(exc):
                raise
    assert last_error is not None
    raise last_error


def _solver_state() -> CTFState:
    manifest = ChallengeManifest(
        name="solver-live-sqli",
        category=Category.WEB,
        difficulty=2,
        vulnerability="SQL injection in a login form",
        description_hint="Minimal login flow with a user lookup query.",
        language="python",
        services=["web server"],
        tools_required=["requests"],
        flag=EXPECTED_FLAG,
    )
    story = ChallengeStory(
        title="The Broken Login",
        description="A small internal portal leaks credentials through a query bug.",
        hints=["Inspect the login flow", "Look for unsanitized SQL"],
        theme="noir",
    )
    code = ChallengeCode(
        files={
            "app.py": VULNERABLE_APP_PY,
            "requirements.txt": "flask\n",
        },
        entry_point="app.py",
        build_notes="",
        flag_location="/flag.txt",
        intended_vulnerability="app.py:login() — unsanitized SQL query",
    )
    infra = ChallengeInfra(
        dockerfile=(
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt /app/requirements.txt\n"
            "COPY app.py /app/app.py\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "CMD [\"python\", \"/app/app.py\"]\n"
        ),
        exposed_ports=[1337],
        startup_command="python /app/app.py",
    )
    return CTFState(
        user_prompt="live solver smoke test",
        story=story,
        manifest=manifest,
        code=code,
        infra=infra,
    )


def _validator_state() -> CTFState:
    manifest = ChallengeManifest(
        name="validator-live-sqli",
        category=Category.WEB,
        difficulty=2,
        vulnerability="SQL injection in a login form",
        description_hint="Minimal login flow with a user lookup query.",
        language="python",
        services=["web server"],
        tools_required=["requests"],
        flag=EXPECTED_FLAG,
    )
    story = ChallengeStory(
        title="The Broken Login",
        description="A small internal portal leaks credentials through a query bug.",
        hints=["Inspect the login flow", "Look for unsanitized SQL"],
        theme="noir",
    )
    code = ChallengeCode(
        files={
            "app.py": VULNERABLE_APP_PY,
            "requirements.txt": "flask\n",
        },
        entry_point="app.py",
        build_notes="",
        flag_location="/flag.txt",
        intended_vulnerability="app.py:login() — unsanitized SQL query",
    )
    infra = ChallengeInfra(
        dockerfile=(
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt /app/requirements.txt\n"
            "COPY app.py /app/app.py\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "CMD [\"python\", \"/app/app.py\"]\n"
        ),
        exposed_ports=[1337],
        startup_command="python /app/app.py",
    )
    solver = ChallengeSolver(
        solve_script=(
            "import os\n"
            "import re\n"
            "import requests\n"
            "host = os.environ.get('TARGET_HOST', 'localhost')\n"
            "url = f'http://{host}:1337/'\n"
            "payload = {\n"
            "    'user': \"admin' OR '1'='1' --\",\n"
            "    'pass': 'x',\n"
            "}\n"
            "resp = requests.post(url, data=payload, timeout=5)\n"
            "match = re.search(r'FLAG:\\s*(CTF\\{[^}]+\\})', resp.text)\n"
            "if not match:\n"
            "    raise SystemExit('flag not found')\n"
            "print(match.group(1))\n"
        ),
        dependencies=[],
        expected_flag=EXPECTED_FLAG,
        solve_steps=["send SQLi payload", "parse flag from response", "print flag"],
    )
    return CTFState(
        user_prompt="live validator smoke test",
        story=story,
        manifest=manifest,
        code=code,
        infra=infra,
        solver=solver,
        event=EventConfig(
            name="Live Test Event",
            flag_regex=r"^CTF\{[A-Za-z0-9_-]{8,}\}$",
            rag_top_k=1,
            use_sandbox=True,
        ),
    )


def _assert_reasonable_solver_output(state: CTFState) -> None:
    assert state.solver is not None
    assert state.solver.expected_flag == EXPECTED_FLAG
    assert state.solver.solve_script.strip()
    assert len(state.solver.solve_steps) >= 2
    assert not any(token in state.solver.solve_script.lower() for token in ("todo", "fixme", "pass #", "lorem ipsum"))
    assert any(
        token in state.solver.solve_script.lower()
        for token in ("requests", "socket", "http", "urllib", "pwntools", "target_host")
    )


def _assert_reasonable_validation_output(state: CTFState) -> None:
    assert state.validation is not None
    assert state.validation.passed is True
    assert state.validation.flag_captured is True
    assert state.validation.errors == []
    assert state.validation.retry_target == RetryTarget.DEVELOPER

    checks = {check.check: check for check in state.validation.checks}
    for expected in {
        "flag_not_in_source",
        "flag_matches_regex",
        "docker_build",
        "docker_network_create",
        "container_start",
        "solver_run",
        "flag_captured",
    }:
        assert expected in checks, f"missing validation check {expected!r}"
        assert checks[expected].passed is True, f"check {expected!r} failed: {checks[expected].detail}"


def _print_solver_output(state: CTFState) -> None:
    assert state.solver is not None
    print("\n=== Solver output ===")
    print(f"expected_flag: {state.solver.expected_flag}")
    print(f"solve_steps: {state.solver.solve_steps}")
    print("solve_script:")
    print(state.solver.solve_script.rstrip())


def _print_validator_output(state: CTFState) -> None:
    assert state.validation is not None
    print("\n=== Validator output ===")
    print(state.validation.model_dump_json(indent=2))


def test_solver_live_smoke(monkeypatch):
    """Run the Solver agent live with RAG stubbed and assert the output is structured.

    This is a cheap but real agent test: the live model produces the solve script,
    while the knowledge-base retrieval is stubbed so we don't spend DB time or
    Gemini embedding tokens.
    """
    _mirror_gemini_key(monkeypatch)

    solver_node = _import_live_node("graph.nodes.solver_node")

    monkeypatch.setattr(
        solver_node,
        "retrieve_similar_challenges",
        lambda *_args, **_kwargs: "## Similar challenges\n\n### Stub [web, difficulty 2]\n**id:** `kb-stub`\n**Languages:** python\n**Description:** Minimal login exploit pattern.",
    )

    result = asyncio.run(_run_with_fallbacks(_solver_state(), solver_node.run, "solver"))
    _assert_reasonable_solver_output(result)
    _print_solver_output(result)


@dataclass
class _FakeSandbox:
    state: CTFState

    @staticmethod
    def available() -> bool:
        return True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def verify(self):
        return (
            [
                ValidationCheck(check="docker_build", passed=True, detail="ok"),
                ValidationCheck(check="docker_network_create", passed=True, detail="ok"),
                ValidationCheck(check="container_start", passed=True, detail="ok"),
                ValidationCheck(check="solver_run", passed=True, detail="exit 0"),
                ValidationCheck(check="flag_captured", passed=True, detail="flag found in solver stdout"),
            ],
            True,
            f"FLAG: {self.state.solver.expected_flag}\n",
        )


def test_validator_live_smoke(monkeypatch):
    """Run the Validator agent live with a fake sandbox and assert the result is sane.

    The fake sandbox keeps this cheap and deterministic while still exercising
    the validator's live LLM review and its deterministic pre-checks.
    """
    _mirror_gemini_key(monkeypatch)

    validator_node = _import_live_node("graph.nodes.validator_node")

    monkeypatch.setattr(validator_node, "DockerSandbox", _FakeSandbox)

    result = asyncio.run(_run_with_fallbacks(_validator_state(), validator_node.run, "validator"))
    _assert_reasonable_validation_output(result)
    _print_validator_output(result)
