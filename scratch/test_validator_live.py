"""Live test for the Validator agent — Layer 3 of docs/testing-e2e.md.

Validator is the special case: it needs all four upstream outputs
(manifest, code, infra, solver) AND a running Docker daemon to perform
the deterministic build / run / solve checks. The LLM-review step at
the end also costs ~1 LLM call.

Required:
  - docker compose -f infrastructure/docker-compose.yml up -d  (for RAG;
    Validator itself only needs the Docker daemon, not pgvector)
  - GEMINI_API_KEY in .env (for the LLM-review)
  - A running Docker daemon (`docker info` succeeds)

Costs ~1 LLM call for the review step. The Docker build + solve loop
adds ~30-60s depending on image cache.

Run from the repo root:
    PYTHONPATH=. uv run python scratch/test_validator_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env explicitly — don't rely on import side-effects.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.schemas import (  # noqa: E402
    Category,
    ChallengeCode,
    ChallengeInfra,
    ChallengeManifest,
    ChallengeSolver,
    CTFState,
)
from graph.nodes import validator_node  # noqa: E402

AGENT_NAME = "Validator"
USER_PROMPT = "A web challenge with cookie tampering"


def _seed_state() -> CTFState:
    """Build the CTFState the Validator agent expects.

    Validator needs `manifest` + `code` + `infra` + `solver`. We hand-seed all
    four for the cookie-cutter challenge. The seeded artifacts MUST be
    self-consistent — the solve script must actually exploit the seeded code.
    """
    manifest = ChallengeManifest(
        name="cookie-cutter",
        category=Category.WEB,
        difficulty=1,
        vulnerability="Unsigned session cookie — role field is plaintext-tamperable",
        description_hint=(
            "Flask app reads a 'role' field directly from a cookie without any "
            "HMAC/signing. Setting role=admin grants access to /flag."
        ),
        language="python",
        services=["web server"],
        tools_required=["python requests"],
        flag="CTF{c00ki3s_n33d_s1gn1ng_n0t_just_h0p3}",
        rag_references=[],
    )

    app_py = '''\
from flask import Flask, request, make_response

app = Flask(__name__)
FLAG = open("/flag").read().strip()


@app.route("/")
def index():
    role = request.cookies.get("role", "guest")
    if role == "admin":
        return f"Welcome, admin. The flag is: {FLAG}"
    return make_response(f"Hi {role}. Only admins can see the flag.")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
'''

    code = ChallengeCode(
        files={
            "app.py": app_py,
            "requirements.txt": "flask==3.0.3\n",
        },
        entry_point="python app.py",
        build_notes="",
        flag_location="The flag is mounted into the container at /flag at runtime.",
        intended_vulnerability=(
            "app.py:8 — index() reads role from cookie without a signature check."
        ),
    )

    infra = ChallengeInfra(
        dockerfile=(
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "COPY . .\n"
            "EXPOSE 8000\n"
            "CMD [\"python\", \"app.py\"]\n"
        ),
        compose_file=None,
        exposed_ports=[8000],
        startup_command="python app.py",
        build_args={},
    )

    # Solve script: send GET / with role=admin cookie, scrape flag from body.
    solve_script = '''\
import re
import sys
import requests

target = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
r = requests.get(target, cookies={"role": "admin"}, timeout=10)
m = re.search(r"CTF\\{[^}]+\\}", r.text)
if not m:
    print("NO FLAG FOUND in:", r.text[:200])
    sys.exit(1)
print(m.group(0))
'''

    solver = ChallengeSolver(
        solve_script=solve_script,
        solve_language="python",
        dependencies=["requests"],
        expected_flag=manifest.flag,
        solve_steps=[
            "GET / with cookie role=admin",
            "Regex-extract CTF{...} from the response body",
            "Print the captured flag",
        ],
    )

    state = CTFState(user_prompt=USER_PROMPT)
    state.manifest = manifest
    state.code = code
    state.infra = infra
    state.solver = solver
    # Run Validator with sandbox enabled to exercise the Docker build/run/solve
    # path. Set use_sandbox=False if Docker isn't available locally.
    state.use_sandbox = True
    return state


async def main() -> None:
    state = _seed_state()
    print(f">>> Running {AGENT_NAME} agent")
    print(
        f"    Seeded state: manifest={state.manifest.name!r}, "
        f"code=[{', '.join(state.code.files)}], "
        f"infra.ports={state.infra.exposed_ports}, "
        f"solver.deps={state.solver.dependencies}, "
        f"use_sandbox={state.use_sandbox}"
    )

    state = await validator_node.run(state)

    output = state.validation
    if output is None:
        print(f"FAIL: state.validation is None after {AGENT_NAME} ran")
        sys.exit(1)

    print(f"\n=== ValidationResult output ===")
    print(output.model_dump_json(indent=2))

    print(f"\n=== Contract checks ===")
    print(f"passed            = {output.passed}")
    print(f"retry_target      = {output.retry_target.value if output.retry_target else None!r}")
    print(f"checks            = {len(output.checks)} total")
    for c in output.checks:
        status = "PASS" if c.passed else "FAIL"
        print(f"    [{status}] {c.check}")
    # Validator must always produce at least the flag_in_source check, even
    # when Docker is unavailable.
    check_names = {c.check for c in output.checks}
    assert "flag_not_in_source" in check_names, (
        "Validator skipped the flag_not_in_source check"
    )
    print(f"\n{AGENT_NAME} contract checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
