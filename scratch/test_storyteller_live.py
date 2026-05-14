"""Live test for the Storyteller agent — Layer 3 of docs/testing-e2e.md.

Storyteller needs a `manifest`. It does NOT hit RAG or Docker. We hand-seed a
realistic ChallengeManifest below to test in isolation; in the full pipeline
this would be the Architect's output.

Costs ~1 LLM call. Burns one daily-quota slot on the chosen Gemini model.

Run from the repo root:
    PYTHONPATH=. uv run python scratch/test_storyteller_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env explicitly — don't rely on import side-effects.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.schemas import Category, ChallengeManifest, CTFState  # noqa: E402
from graph.nodes import storyteller_node  # noqa: E402

AGENT_NAME = "Storyteller"
USER_PROMPT = "A noir-themed crypto challenge"


def _seed_state() -> CTFState:
    """Build the CTFState the Storyteller agent expects.

    Storyteller needs `manifest` (normally produced by the Architect upstream).
    We hand-seed a realistic AES-GCM-nonce-reuse manifest so this script can
    run standalone without invoking the Architect.
    """
    manifest = ChallengeManifest(
        name="vault-of-whispers",
        category=Category.CRYPTO,
        difficulty=3,
        vulnerability=(
            "AES-GCM nonce reuse — same nonce reused across messages "
            "allows XOR-of-ciphertexts attack"
        ),
        description_hint="Server encrypts user messages with AES-GCM but reuses a fixed nonce.",
        language="python",
        services=["tcp socket"],
        tools_required=["pwntools", "python crypto library"],
        flag="CTF{n0nce_r3us3_is_d34dly_4lways_rotate}",
        rag_references=[],
    )
    state = CTFState(user_prompt=USER_PROMPT)
    state.manifest = manifest
    return state


async def main() -> None:
    state = _seed_state()
    print(f">>> Running {AGENT_NAME} agent")
    print(f"    Seeded state: manifest={state.manifest.name!r}")

    state = await storyteller_node.run(state)

    output = state.story
    if output is None:
        print(f"FAIL: state.story is None after {AGENT_NAME} ran")
        sys.exit(1)

    print(f"\n=== ChallengeStory output ===")
    print(output.model_dump_json(indent=2))

    print(f"\n=== Contract checks ===")
    print(f"title             = {output.title!r}")
    print(f"theme             = {output.theme!r}")
    print(f"description       = ({len(output.description)} chars)")
    print(f"hints             = {len(output.hints)} entries")
    assert output.title, "Storyteller produced empty title"
    assert output.description, "Storyteller produced empty description"
    assert len(output.hints) >= 1, "Storyteller produced no hints"
    print(f"\n{AGENT_NAME} contract checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
