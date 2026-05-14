"""Live test for the Solver agent — Layer 3 of docs/testing-e2e.md.

Solver needs `manifest`, `code`, AND `infra`. It hits the live RAG retriever
(category + vulnerability + 'exploit solve' is the query), which requires:
  - docker compose -f infrastructure/docker-compose.yml up -d
  - GEMINI_API_KEY in .env
  - A populated `challenges` table (`python -m indexing.indexer`)

Costs ~1 LLM call. Burns one daily-quota slot on the chosen Gemini model.

Run from the repo root:
    PYTHONPATH=. uv run python scratch/test_solver_live.py
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
    CTFState,
)
from graph.nodes import solver_node  # noqa: E402

AGENT_NAME = "Solver"
USER_PROMPT = "A web challenge with cookie tampering"


def _seed_state() -> CTFState:
    """Build the CTFState the Solver agent expects.

    Solver needs `manifest` + `code` + `infra`. We hand-seed all three using
    the same cookie-tampering challenge as the Developer / DevOps tests.
    Notably, the seeded `code` does NOT leak the flag (unlike the Developer
    agent's output — see #14); we mount it at runtime via the Dockerfile,
    matching the parent-document retrieval / runtime-flag-mount convention.
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
from flask import Flask, request, make_response, redirect

app = Flask(__name__)
FLAG = open("/flag").read().strip()


@app.route("/")
def index():
    role = request.cookies.get("role", "guest")
    if role == "admin":
        return f"Welcome, admin. The flag is: {FLAG}"
    resp = make_response(f"Hi {role}. Only admins can see the flag.")
    return resp


@app.route("/login")
def login():
    resp = redirect("/")
    resp.set_cookie("role", "guest")
    return resp


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

    state = CTFState(user_prompt=USER_PROMPT)
    state.manifest = manifest
    state.code = code
    state.infra = infra
    return state


async def main() -> None:
    state = _seed_state()
    print(f">>> Running {AGENT_NAME} agent")
    print(
        f"    Seeded state: manifest={state.manifest.name!r}, "
        f"code=[{', '.join(state.code.files)}], "
        f"infra.ports={state.infra.exposed_ports}"
    )

    state = await solver_node.run(state)

    output = state.solver
    if output is None:
        print(f"FAIL: state.solver is None after {AGENT_NAME} ran")
        sys.exit(1)

    print(f"\n=== ChallengeSolver output ===")
    summary = output.model_dump()
    summary["solve_script"] = f"<{len(output.solve_script)} chars>"
    import json
    print(json.dumps(summary, indent=2))

    print(f"\n=== Contract checks ===")
    print(f"solve_language    = {output.solve_language!r}")
    print(f"solve_script      = ({len(output.solve_script)} chars)")
    print(f"dependencies      = {output.dependencies}")
    print(f"expected_flag     = {output.expected_flag!r}")
    assert output.solve_script, "Solver produced empty solve_script"
    assert output.expected_flag, "Solver produced empty expected_flag"
    # The Solver's expected_flag should match the manifest's flag exactly —
    # that's the contract the Validator's flag check relies on.
    assert output.expected_flag == state.manifest.flag, (
        f"Solver's expected_flag {output.expected_flag!r} != manifest's "
        f"flag {state.manifest.flag!r}"
    )
    print(f"\n{AGENT_NAME} contract checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
