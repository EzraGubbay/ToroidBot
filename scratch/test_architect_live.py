"""Live test for the Architect agent — Layer 3 of docs/testing-e2e.md.

Architect needs only `user_prompt` (and optionally `event`). It hits the live
RAG retriever, which requires:
  - docker compose -f infrastructure/docker-compose.yml up -d
  - GEMINI_API_KEY in .env (used for embedding the query + agent chat completion)
  - A populated `challenges` table (`python -m indexing.indexer`)

Costs ~1 LLM call. Burns one daily-quota slot on the chosen Gemini model.

Run from the repo root:
    PYTHONPATH=. uv run python scratch/test_architect_live.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env explicitly — don't rely on import side-effects.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.schemas import CTFState  # noqa: E402
from graph.nodes import architect_node  # noqa: E402

AGENT_NAME = "Architect"
USER_PROMPT = "a beginner crypto challenge involving caesar cipher"


def _seed_state() -> CTFState:
    """Build the CTFState the Architect agent expects.

    Architect is the head of the pipeline, so only `user_prompt` is needed.
    No `event`, no upstream outputs.
    """
    return CTFState(user_prompt=USER_PROMPT)


async def main() -> None:
    state = _seed_state()
    print(f">>> Running {AGENT_NAME} agent")
    print(f"    Seeded state: user_prompt={USER_PROMPT!r}")

    state = await architect_node.run(state)

    output = state.manifest
    if output is None:
        print(f"FAIL: state.manifest is None after {AGENT_NAME} ran")
        sys.exit(1)

    print(f"\n=== ChallengeManifest output ===")
    print(output.model_dump_json(indent=2))

    print(f"\n=== Contract checks ===")
    print(f"name              = {output.name!r}")
    print(f"category          = {output.category.value}")
    print(f"difficulty        = {output.difficulty} (bounds: 1-5)")
    print(f"flag              = {output.flag!r}")
    print(f"rag_references    = {output.rag_references}")
    assert 1 <= output.difficulty <= 5, "Architect violated difficulty bounds"
    assert output.flag.startswith("CTF{") and output.flag.endswith("}"), (
        "default flag format violated"
    )
    print(f"\n{AGENT_NAME} contract checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
