"""DevOps node — generates Dockerfile and deployment config."""

from __future__ import annotations

from agents.schemas import ChallengeInfra, CTFState


def _build_devops_prompt(state: CTFState) -> str:
    if state.manifest is None:
        raise RuntimeError("Architect must run before DevOps")
    if state.code is None:
        raise RuntimeError("Developer must run before DevOps")

    parts = [
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}",
        f"Challenge code:\n{state.code.model_dump_json(indent=2)}",
    ]

    if state.validation and not state.validation.passed:
        sections = ["## PREVIOUS ATTEMPT FAILED"]
        if state.validation.errors:
            sections.append("Concrete failures from the validator:")
            sections.extend(f"  - {e}" for e in state.validation.errors)
        if state.validation.retry_instructions:
            sections.append("Fix instructions:")
            sections.append(state.validation.retry_instructions)
        if state.infra is not None:
            sections.append("Previous infrastructure output (fix this, don't regenerate blindly):")
            sections.append(state.infra.model_dump_json(indent=2))
        sections.append(
            "Preserve what already works. Apply minimal targeted edits to Dockerfile/compose "
            "to satisfy deterministic validator checks (build, start, run solver)."
        )
        parts.append("\n".join(sections))

    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the DevOps agent to produce ChallengeInfra."""
    from agents.factory import create_agent

    if state.manifest is None:
        raise RuntimeError("Architect must run before DevOps")
    if state.code is None:
        raise RuntimeError("Developer must run before DevOps")

    agent = create_agent("devops_infra", ChallengeInfra, model=state.model_for("devops"))

    prompt = _build_devops_prompt(state)
    result = await agent.run(prompt)

    state.infra = result.output
    return state
