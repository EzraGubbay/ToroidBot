"""Solver node — writes an exploit script that proves the challenge is solvable."""

from __future__ import annotations

from agents.schemas import ChallengeSolver, CTFState


def _build_solver_prompt(state: CTFState, rag_context: str) -> str:
    assert state.manifest is not None and state.code is not None and state.infra is not None

    parts = [
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}",
        f"Challenge code:\n{state.code.model_dump_json(indent=2)}",
        f"Challenge infra:\n{state.infra.model_dump_json(indent=2)}",
        f"Similar exploits from knowledge base (study these for technique patterns):\n{rag_context}",
    ]

    solve_path = state.manifest.intended_solve_path.strip()
    if solve_path:
        parts.append(
            "## YOUR EXPLOIT MUST FOLLOW THIS PATH (intended_solve_path)\n"
            f"{solve_path}\n\n"
            "For each numbered step, locate the code in `Challenge code` above that "
            "makes the step possible, then write the script to perform that exact "
            "step. Do not invent a shortcut. If the code does not appear to support "
            "a step, still write the script following the path — the validator will "
            "catch the mismatch and re-run the Developer rather than letting you "
            "paper over it."
        )

    if state.solver is not None and state.validation and not state.validation.passed:
        sections = ["## YOUR PREVIOUS SOLVE SCRIPT FAILED"]
        sections.append("Previous solve_script:\n```\n" + state.solver.solve_script + "\n```")
        if state.validation.errors:
            sections.append("Validator errors:")
            sections.extend(f"  - {e}" for e in state.validation.errors)

        solver_related = [
            c for c in state.validation.checks
            if not c.passed and c.check in {"solver_run", "flag_captured"}
        ]
        if solver_related:
            sections.append("Likely solver-script issues (from deterministic checks):")
            sections.extend(f"  - {c.check}: {c.detail}" for c in solver_related)
            sections.append(
                "Before rewriting, identify what in the previous solve_script could "
                "cause these failures (wrong route, wrong regex, wrong port, wrong "
                "timing, or parsing assumptions), then fix those specifics."
            )

        if state.failed_solver_scripts:
            previous_failed = state.failed_solver_scripts[-1]
            if previous_failed != state.solver.solve_script:
                sections.append(
                    "Most recent failed solver script from retry history:\n```\n"
                    + previous_failed + "\n```"
                )

        sections.append(
            "Either the script was wrong or the Developer's code changed underneath "
            "you. Re-read the latest `Challenge code` above and write a fresh script "
            "that follows intended_solve_path against the current code."
        )
        parts.append("\n".join(sections))

    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the Solver agent to produce a ChallengeSolver."""
    from agents.factory import create_agent
    from orchestrator.rag import retrieve_similar_challenges

    if state.manifest is None:
        raise RuntimeError("Architect must run before Solver")
    if state.code is None:
        raise RuntimeError("Developer must run before Solver")
    if state.infra is None:
        raise RuntimeError("DevOps must run before Solver")

    top_k = state.event.rag_top_k if state.event else 3
    rag_context = retrieve_similar_challenges(
        f"{state.manifest.category} {state.manifest.vulnerability} exploit solve",
        top_k=top_k,
    )

    agent = create_agent("exploit_solver", ChallengeSolver, model=state.model_for("solver"))

    prompt = _build_solver_prompt(state, rag_context)
    result = await agent.run(prompt)

    state.solver = result.output
    return state
