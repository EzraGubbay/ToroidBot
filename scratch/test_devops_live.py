"""Live test for the DevOps agent — Layer 3 of docs/testing-e2e.md.

DevOps needs `manifest` AND `code`. It does NOT hit RAG or Docker. We
hand-seed both — in the full pipeline `manifest` comes from the Architect
and `code` from the Developer.

Costs ~1 LLM call. Burns one daily-quota slot on the chosen Gemini model.

Run from the repo root:
    PYTHONPATH=. uv run python scratch/test_devops_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env explicitly — don't rely on import side-effects.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.schemas import Category, ChallengeCode, ChallengeManifest, CTFState  # noqa: E402
from graph.nodes import devops_node  # noqa: E402

AGENT_NAME = "DevOps"
USER_PROMPT = "A web challenge with cookie tampering"


def _seed_state() -> CTFState:
    """Build the CTFState the DevOps agent expects.

    DevOps needs `manifest` + `code`. We hand-seed a minimal but realistic
    Flask app whose vulnerability is an unsigned cookie that the player can
    tamper with to escalate to admin and read the flag.
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
        tools_required=["browser dev tools", "curl"],
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
    resp = make_response(
        f"Hi {role}. Only admins can see the flag. <a href='/login'>login</a>"
    )
    return resp


@app.route("/login")
def login():
    resp = redirect("/")
    # BUG: role baked into cookie without any signing.
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
        flag_location="The flag is mounted into the container at /flag and read by app.py at startup.",
        intended_vulnerability=(
            "app.py:8-14 — the index() route reads role straight from a cookie "
            "with no signature check. Player sets role=admin via the browser."
        ),
    )

    state = CTFState(user_prompt=USER_PROMPT)
    state.manifest = manifest
    state.code = code
    return state


async def main() -> None:
    state = _seed_state()
    print(f">>> Running {AGENT_NAME} agent")
    print(f"    Seeded state: manifest={state.manifest.name!r}, code=[{', '.join(state.code.files)}]")

    state = await devops_node.run(state)

    output = state.infra
    if output is None:
        print(f"FAIL: state.infra is None after {AGENT_NAME} ran")
        sys.exit(1)

    print(f"\n=== ChallengeInfra output ===")
    print(output.model_dump_json(indent=2))

    print(f"\n=== Contract checks ===")
    print(f"dockerfile        = ({len(output.dockerfile)} chars)")
    print(f"compose_file      = {('set' if output.compose_file else 'None')}")
    print(f"exposed_ports     = {output.exposed_ports}")
    print(f"startup_command   = {output.startup_command!r}")
    print(f"build_args        = {output.build_args}")
    assert output.dockerfile, "DevOps produced empty Dockerfile"
    assert "FROM " in output.dockerfile.upper() or "FROM " in output.dockerfile, (
        "Dockerfile missing FROM directive"
    )
    assert output.exposed_ports, "DevOps produced no exposed ports for a web challenge"
    assert output.startup_command, "DevOps produced empty startup_command"
    print(f"\n{AGENT_NAME} contract checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
