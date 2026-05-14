"""Architect node — designs the challenge concept using RAG context."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeManifest, CTFState
from orchestrator.rag import retrieve_similar_challenges


async def run(state: CTFState) -> CTFState:
    """Run the Architect agent to produce a ChallengeManifest."""
    rag_context = retrieve_similar_challenges(state.user_prompt)

    agent = create_agent("rag_architect", ChallengeManifest, model=state.model)

    prompt = f"User request: {state.user_prompt}\n\nSimilar challenges from knowledge base:\n{rag_context}"
    result = await agent.run(prompt)

    state.manifest = result.output
    return state
