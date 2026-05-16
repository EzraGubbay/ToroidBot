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
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env explicitly — don't rely on import side-effects.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents.schemas import CTFState  # noqa: E402
from graph.nodes import architect_node  # noqa: E402
from orchestrator.rag import retrieve_similar_challenges  # noqa: E402

AGENT_NAME = "Architect"
USER_PROMPT = "a beginner crypto challenge involving caesar cipher"


def _print_rag_matches(prompt: str, top_k: int = 3) -> None:
    """Fetch and print RAG neighbors so live runs are debuggable."""
    rag_context = retrieve_similar_challenges(prompt, top_k=top_k)

    print("\n=== RAG retrieval (nearest matches) ===")
    print(rag_context)

    # Compact summary: title + id lines from the formatted RAG context.
    title_re = re.compile(r"^###\s+(.+?)\s+\[[^\]]+\]$")
    id_re = re.compile(r"^\*\*id:\*\*\s+`([^`]+)`$")
    titles: list[str] = []
    ids: list[str] = []
    for line in rag_context.splitlines():
        m_title = title_re.match(line.strip())
        if m_title:
            titles.append(m_title.group(1))
            continue
        m_id = id_re.match(line.strip())
        if m_id:
            ids.append(m_id.group(1))

    if titles or ids:
        print("\n=== RAG summary ===")
        if titles:
            print(f"names: {titles}")
        if ids:
            print(f"ids:   {ids}")


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

    _print_rag_matches(USER_PROMPT, top_k=3)

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
