"""Live test for the Developer agent — Layer 3 of docs/testing-e2e.md.

Developer needs `manifest` AND `story`. It hits the live RAG retriever
(category + vulnerability + language is the query), which requires:
  - docker compose -f infrastructure/docker-compose.yml up -d
  - GEMINI_API_KEY in .env
  - A populated `challenges` table (`python -m indexing.indexer`)

Costs ~1 LLM call. Burns one daily-quota slot on the chosen Gemini model.

Run from the repo root:
    PYTHONPATH=. uv run python scratch/test_developer_live.py
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
    ChallengeManifest,
    ChallengeStory,
    CTFState,
)
from graph.nodes import developer_node  # noqa: E402

AGENT_NAME = "Developer"
USER_PROMPT = "A web challenge with cookie tampering"


def _seed_state() -> CTFState:
    """Build the CTFState the Developer agent expects.

    Developer needs `manifest` + `story`. We hand-seed both for the same
    cookie-tampering challenge used in the DevOps test, so the chained
    behavior across agents is consistent.
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
    story = ChallengeStory(
        title="Cookie Cutter Corp",
        description=(
            "Cookie Cutter Corp's hot new web app proudly broadcasts that everyone is "
            "treated equal — guest, member, admin alike. But rumor has it the front-desk "
            "intern wired the role-check before the security audit shipped. Can you spot "
            "the shortcut and walk past the velvet rope?"
        ),
        hints=[
            "All session state is round-tripped through the browser. What if you edited it on the way back?",
            "Try changing the value of the role cookie before sending it.",
            "The server checks `role == 'admin'` after reading the cookie verbatim — no signature, no HMAC.",
        ],
        theme="Corporate",
    )

    state = CTFState(user_prompt=USER_PROMPT)
    state.manifest = manifest
    state.story = story
    return state


async def main() -> None:
    state = _seed_state()
    print(f">>> Running {AGENT_NAME} agent")
    print(f"    Seeded state: manifest={state.manifest.name!r}, story={state.story.title!r}")

    state = await developer_node.run(state)

    output = state.code
    if output is None:
        print(f"FAIL: state.code is None after {AGENT_NAME} ran")
        sys.exit(1)

    print(f"\n=== ChallengeCode output ===")
    # Truncate file contents in the JSON dump so the terminal isn't flooded.
    summary = output.model_dump()
    summary["files"] = {
        name: f"<{len(content)} chars>" for name, content in summary["files"].items()
    }
    import json
    print(json.dumps(summary, indent=2))

    print(f"\n=== Contract checks ===")
    print(f"files             = {list(output.files)}")
    print(f"entry_point       = {output.entry_point!r}")
    print(f"flag_location     = ({len(output.flag_location)} chars)")
    print(f"vulnerability     = ({len(output.intended_vulnerability)} chars)")
    print(f"build_notes       = ({len(output.build_notes)} chars)")
    assert output.files, "Developer produced no files"
    assert output.entry_point, "Developer produced empty entry_point"
    assert output.flag_location, "Developer produced empty flag_location"
    assert output.intended_vulnerability, "Developer didn't restate the vulnerability"
    # The seeded manifest's flag must NOT appear in source — that's the Validator's
    # deterministic flag-in-source check. Sanity-check it here so we catch the
    # easy regression before the Validator does.
    seeded_flag = state.manifest.flag
    leaked = [name for name, content in output.files.items() if seeded_flag in content]
    assert not leaked, f"Developer leaked the flag into source files: {leaked}"
    print(f"\n{AGENT_NAME} contract checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
