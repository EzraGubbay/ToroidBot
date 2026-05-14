"""Linear pipeline that chains agents: Architect → Storyteller → Developer → DevOps → Solver → Validator.

The Validator can trigger retries back to the Developer node.
"""

from __future__ import annotations

from agents.schemas import CTFState
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

    Runs each agent in sequence. If the Validator fails, retries from
    the Developer node up to state.max_retries times.
    """
    # Phase 1: Design (runs once)
    state = await architect_node.run(state)
    state = await storyteller_node.run(state)

    # Phase 2: Build → Verify (retryable)
    while True:
        state = await developer_node.run(state)
        state = await devops_node.run(state)
        state = await solver_node.run(state)
        state = await validator_node.run(state)

        if state.validation and state.validation.passed:
            break

        state.retry_count += 1
        if state.retry_count >= state.max_retries:
            break

    return state
