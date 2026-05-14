"""Developer node — writes the vulnerable source code."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeCode, CTFState


async def run(state: CTFState) -> CTFState:
    """Run the Developer agent to produce ChallengeCode."""
    assert state.manifest is not None, "Architect must run before Developer"
    assert state.story is not None, "Storyteller must run before Developer"

    agent = create_agent("ctf_developer", ChallengeCode, model=state.model)

    prompt_parts = [
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}",
        f"Challenge story:\n{state.story.model_dump_json(indent=2)}",
    ]

    # If retrying, include validator feedback
    if state.validation and state.validation.retry_instructions:
        prompt_parts.append(
            f"PREVIOUS ATTEMPT FAILED. Fix these issues:\n{state.validation.retry_instructions}"
        )

    result = await agent.run("\n\n".join(prompt_parts))

    state.code = result.output
    return state
