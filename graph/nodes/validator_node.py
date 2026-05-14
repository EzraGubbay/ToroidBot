"""Validator node — quality gate that checks the challenge works correctly."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import CTFState, ValidationResult


async def run(state: CTFState) -> CTFState:
    """Run the Validator agent to produce a ValidationResult."""
    assert state.manifest is not None
    assert state.code is not None
    assert state.infra is not None
    assert state.solver is not None

    agent = create_agent("validator", ValidationResult, model=state.model)

    prompt = (
        f"Validate this challenge. Full pipeline state:\n\n"
        f"Manifest:\n{state.manifest.model_dump_json(indent=2)}\n\n"
        f"Story:\n{state.story.model_dump_json(indent=2) if state.story else 'N/A'}\n\n"
        f"Code:\n{state.code.model_dump_json(indent=2)}\n\n"
        f"Infra:\n{state.infra.model_dump_json(indent=2)}\n\n"
        f"Solver:\n{state.solver.model_dump_json(indent=2)}"
    )
    result = await agent.run(prompt)

    state.validation = result.output
    return state
