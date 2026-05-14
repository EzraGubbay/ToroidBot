"""Architect node — designs the challenge concept using RAG context."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeManifest, CTFState
from orchestrator.rag import retrieve_similar_challenges


def _build_architect_prompt(state: CTFState, rag_context: str) -> str:
    parts = [
        f"User request: {state.user_prompt}",
        f"Similar challenges from knowledge base:\n{rag_context}",
    ]
    if state.event is not None:
        ev = state.event
        ev_lines = [
            "## EVENT CONSTRAINTS (hard requirements)",
            f"Flag must match this regex: {ev.flag_regex}",
            f"Audience: {ev.audience.value}",
        ]
        if ev.theme:
            ev_lines.append(f"Theme: {ev.theme}")
        if ev.forbidden_categories:
            cats = ", ".join(c.value for c in ev.forbidden_categories)
            ev_lines.append(f"Forbidden categories (do not pick): {cats}")
        if ev.forbidden_techniques:
            techs = ", ".join(ev.forbidden_techniques)
            ev_lines.append(f"Forbidden techniques: {techs}")
        parts.append("\n".join(ev_lines))
    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the Architect agent to produce a ChallengeManifest."""
    top_k = state.event.rag_top_k if state.event else 3
    rag_context = retrieve_similar_challenges(state.user_prompt, top_k=top_k)

    agent = create_agent("rag_architect", ChallengeManifest, model=state.model_for("architect"))

    prompt = _build_architect_prompt(state, rag_context)
    result = await agent.run(prompt)

    state.manifest = result.output
    return state
