"""Developer node — writes the vulnerable source code."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeCode, CTFState
from orchestrator.rag import retrieve_similar_challenges


def _build_developer_prompt(state: CTFState, rag_context: str) -> str:
    if state.manifest is None:
        raise RuntimeError("Architect must run before Developer")
    if state.story is None:
        raise RuntimeError("Storyteller must run before Developer")

    parts = [
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}",
        f"Challenge story:\n{state.story.model_dump_json(indent=2)}",
        f"Similar challenges from knowledge base (study these for implementation patterns):\n{rag_context}",
    ]

    if state.event is not None and state.event.forbidden_techniques:
        techs = ", ".join(state.event.forbidden_techniques)
        parts.append(
            "## EVENT CONSTRAINTS\n"
            f"Forbidden techniques (do not use): {techs}"
        )

    if state.validation and state.validation.retry_instructions:
        parts.append(
            f"PREVIOUS ATTEMPT FAILED. Fix these issues:\n{state.validation.retry_instructions}"
        )

    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the Developer agent to produce ChallengeCode."""
    if state.manifest is None:
        raise RuntimeError("Architect must run before Developer")
    if state.story is None:
        raise RuntimeError("Storyteller must run before Developer")

    top_k = state.event.rag_top_k if state.event else 3
    rag_context = retrieve_similar_challenges(
        f"{state.manifest.category} {state.manifest.vulnerability} {state.manifest.language}",
        top_k=top_k,
    )

    agent = create_agent("ctf_developer", ChallengeCode, model=state.model_for("developer"))

    prompt = _build_developer_prompt(state, rag_context)
    result = await agent.run(prompt)
    code = result.output

    if not code.files:
        raise RuntimeError(
            "Developer agent returned empty files dict — no source code was generated. "
            "This usually indicates a structured-output failure with the chosen model. "
            f"Model: {state.model_for('developer')}"
        )

    state.code = code
    return state
