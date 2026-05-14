"""DevOps node — generates Dockerfile and deployment config."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeInfra, CTFState


async def run(state: CTFState) -> CTFState:
    """Run the DevOps agent to produce ChallengeInfra."""
    assert state.manifest is not None, "Architect must run before DevOps"
    assert state.code is not None, "Developer must run before DevOps"

    agent = create_agent("devops_infra", ChallengeInfra, model=state.model)

    prompt = (
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}\n\n"
        f"Challenge code:\n{state.code.model_dump_json(indent=2)}"
    )
    result = await agent.run(prompt)

    state.infra = result.output
    return state
