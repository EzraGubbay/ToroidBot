"""Linear pipeline that chains agents: Architect → Storyteller → Developer → DevOps → Solver → Validator.

On validation failure, the pipeline reruns the appropriate stage based on
`state.validation.retry_target`:
- developer → developer + devops + solver
- devops    → devops + solver
- solver    → solver only
"""

from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable

from agents.schemas import CTFState, RetryTarget
from graph.nodes import (
    architect_node,
    developer_node,
    devops_node,
    solver_node,
    storyteller_node,
    validator_node,
)


async def _run_step(
    name: str,
    fn: Callable[[CTFState], Awaitable[CTFState]],
    state: CTFState,
) -> CTFState:
    """Run one agent node with start/end progress prints.

    Prints to stderr (not stdout) so the "Challenge saved to:" success line
    in main.py — and any JSON parsing of stdout — isn't polluted.
    """
    print(f"[{name}] starting…", file=sys.stderr, flush=True)
    started = time.monotonic()
    state = await fn(state)
    elapsed = time.monotonic() - started
    print(f"[{name}] done in {elapsed:.1f}s", file=sys.stderr, flush=True)
    return state


async def run_pipeline(state: CTFState) -> CTFState:
    """Execute the full challenge generation pipeline.

    `max_retries` is the number of additional attempts after the first run
    (so max_retries=3 means up to 4 total attempts).
    """
    # Phase 1: Design (runs once)
    state = await _run_step("architect", architect_node.run, state)
    state = await _run_step("storyteller", storyteller_node.run, state)

    # Phase 2: Build (runs once initially; partial rerun on retry)
    state = await _run_step("developer", developer_node.run, state)
    state = await _run_step("devops", devops_node.run, state)
    state = await _run_step("solver", solver_node.run, state)
    state = await _run_step("validator", validator_node.run, state)

    while not (state.validation and state.validation.passed):
        if state.retry_count >= state.max_retries:
            break

        # Persist the failing solver script before it is potentially overwritten
        # by the next Solver run.
        if state.solver is not None and state.validation and not state.validation.passed:
            failed_script = state.solver.solve_script
            if failed_script and (
                not state.failed_solver_scripts
                or state.failed_solver_scripts[-1] != failed_script
            ):
                state.failed_solver_scripts.append(failed_script)

        state.retry_count += 1
        print(
            f"[pipeline] retry {state.retry_count}/{state.max_retries} "
            f"(target={state.validation.retry_target.value if state.validation else 'developer'})",
            file=sys.stderr,
            flush=True,
        )

        target = state.validation.retry_target if state.validation else RetryTarget.DEVELOPER
        if target == RetryTarget.SOLVER:
            # Solve script was the problem; code/infra are fine.
            state = await _run_step("solver", solver_node.run, state)
        elif target == RetryTarget.DEVOPS:
            # Infrastructure/build issue; keep code and regenerate infra + solve.
            state = await _run_step("devops", devops_node.run, state)
            state = await _run_step("solver", solver_node.run, state)
        else:
            state = await _run_step("developer", developer_node.run, state)
            state = await _run_step("devops", devops_node.run, state)
            state = await _run_step("solver", solver_node.run, state)
        state = await _run_step("validator", validator_node.run, state)

    return state
