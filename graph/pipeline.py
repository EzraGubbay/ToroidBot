"""Linear pipeline that chains agents: Architect → Storyteller → Developer → DevOps → Solver → Validator.

On validation failure, the pipeline reruns either the Developer or just the Solver
based on `state.validation.retry_target`.
"""

from __future__ import annotations

from agents.schemas import CTFState, RetryTarget
from graph.nodes import (
    architect_node,
    developer_node,
    devops_node,
    solver_node,
    storyteller_node,
    validator_node,
)


async def run_pipeline(state: CTFState) -> CTFState:
    """Execute the full challenge generation pipeline.

    `max_retries` is the number of additional attempts after the first run
    (so max_retries=3 means up to 4 total attempts).
    """
    # Phase 1: Design (runs once)
    state = await architect_node.run(state)
    state = await storyteller_node.run(state)

    # Phase 2: Build (runs once initially; partial rerun on retry)
    state = await developer_node.run(state)
    state = await devops_node.run(state)
    state = await solver_node.run(state)
    state = await validator_node.run(state)

    while not (state.validation and state.validation.passed):
        if state.retry_count >= state.max_retries:
            break
        state.retry_count += 1

        target = state.validation.retry_target if state.validation else RetryTarget.DEVELOPER
        if target == RetryTarget.SOLVER:
            # Solve script was the problem; code/infra are fine.
            state = await solver_node.run(state)
        else:
            state = await developer_node.run(state)
            state = await devops_node.run(state)
            state = await solver_node.run(state)
        state = await validator_node.run(state)

    return state
