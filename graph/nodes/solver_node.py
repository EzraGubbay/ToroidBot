"""Solver node — writes an exploit script that proves the challenge is solvable."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeSolver, CTFState
from orchestrator.rag import retrieve_similar_challenges


async def run(state: CTFState) -> CTFState:
    """Run the Solver agent to produce a ChallengeSolver."""
    assert state.manifest is not None, "Architect must run before Solver"
    assert state.code is not None, "Developer must run before Solver"
    assert state.infra is not None, "DevOps must run before Solver"

    rag_context = retrieve_similar_challenges(
        f"{state.manifest.category} {state.manifest.vulnerability} exploit solve"
    )

    agent = create_agent("exploit_solver", ChallengeSolver, model=state.model)

    prompt = (
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}\n\n"
        f"Challenge code:\n{state.code.model_dump_json(indent=2)}\n\n"
        f"Challenge infra:\n{state.infra.model_dump_json(indent=2)}\n\n"
        f"Similar exploits from knowledge base (study these for technique patterns):\n{rag_context}"
    )
    result = await agent.run(prompt)

    state.solver = result.output
    return state
