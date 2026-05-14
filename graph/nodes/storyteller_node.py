"""Storyteller node — creates the narrative wrapper for the challenge."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeStory, CTFState


async def run(state: CTFState) -> CTFState:
    """Run the Storyteller agent to produce a ChallengeStory."""
    if state.manifest is None:
        raise RuntimeError("Architect must run before Storyteller")

    agent = create_agent("storyteller", ChallengeStory, model=state.model)

    prompt = f"Create a story for this challenge:\n{state.manifest.model_dump_json(indent=2)}"
    result = await agent.run(prompt)

    state.story = result.output
    return state
