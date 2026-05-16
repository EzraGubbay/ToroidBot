"""Developer node — writes the vulnerable source code."""

from __future__ import annotations

from agents.schemas import ChallengeCode, CTFState


def _build_developer_prompt(state: CTFState, rag_context: str) -> str:
    if state.manifest is None:
        raise RuntimeError("Architect must run before Developer")
    if state.story is None:
        raise RuntimeError("Storyteller must run before Developer")

    parts = [
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}",
        f"Challenge story:\n{state.story.model_dump_json(indent=2)}",
        f"Similar challenges from knowledge base (study these for implementation patterns):\n{rag_context}",
    ]

    solve_path = state.manifest.intended_solve_path.strip()
    if solve_path:
        parts.append(
            "## HARD CONTRACT — intended_solve_path\n"
            f"{solve_path}\n\n"
            "Every numbered step must be literally executable against the code you "
            "write. Walk through each step with your code open before submitting. "
            "If a step says 'View HTML source and find <!-- Flag: ... -->', the flag "
            "MUST be in the static HTML response — not injected by JS, not behind an "
            "auth-gated endpoint, not assembled from fragments. Violations fail "
            "validation and force a retry."
        )

    parts.append(
        "## CHALLENGE SAFETY CONTRACT\n"
        "Build exactly one intentional vulnerability: the one requested by the "
        "manifest. Do not add extra user-controlled injection points, hidden APIs, "
        "or alternate solve paths. If the intended flaw is SSTI, every other user-"
        "controlled field rendered into HTML must be escaped or handled safely. Do "
        "not hardcode placeholder secrets such as SECRET_KEY; read secrets from the "
        "environment or generate them at runtime. Do not wrap the vulnerable "
        "rendering path in a blanket exception handler that hides template errors; "
        "the solver must be able to observe render output and debug the intended "
        "interaction. Keep route names, form field names, and solver steps aligned "
        "with the manifest so the challenge does not drift across retries."
    )

    if state.event is not None and state.event.forbidden_techniques:
        techs = ", ".join(state.event.forbidden_techniques)
        parts.append(
            "## EVENT CONSTRAINTS\n"
            f"Forbidden techniques (do not use): {techs}"
        )

    if state.validation and not state.validation.passed:
        sections = ["## PREVIOUS ATTEMPT FAILED"]
        if state.validation.errors:
            sections.append("Concrete failures from the validator:")
            sections.extend(f"  - {e}" for e in state.validation.errors)
        if state.validation.retry_instructions:
            sections.append("Fix instructions:")
            sections.append(state.validation.retry_instructions)
        # If we have the previous generated source, include a truncated copy
        # so the Developer can modify only the failing parts instead of
        # regenerating everything from scratch.
        if getattr(state, "code", None) and state.code.files:
            max_chars = 4096
            sections.append("Previously generated files (each truncated to 4 KB):")
            for fname, content in state.code.files.items():
                key = fname.lower()
                # Don't leak files that should never be included in prompts
                if key in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "flag.txt"}:
                    continue
                snippet = content[:max_chars]
                if len(content) > max_chars:
                    snippet += "\n...[truncated]..."
                sections.append(f"---\nFile: {fname}\n{snippet}\n---")

        sections.append(
            "Fix these specifically. Do not regenerate from scratch — preserve what "
            "worked in the previous attempt and change only what the validator flagged."
        )
        parts.append("\n".join(sections))

    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the Developer agent to produce ChallengeCode."""
    from agents.factory import create_agent
    from orchestrator.rag import retrieve_similar_challenges

    if state.manifest is None:
        raise RuntimeError("Architect must run before Developer")
    if state.story is None:
        raise RuntimeError("Storyteller must run before Developer")

    top_k = state.event.rag_top_k if state.event else 3
    rag_context = retrieve_similar_challenges(
        f"{state.manifest.category} {state.manifest.vulnerability} {state.manifest.language}",
        top_k=top_k,
    )

    agent = create_agent("ctf_developer", ChallengeCode, model=state.model_for("developer"))

    prompt = _build_developer_prompt(state, rag_context)
    result = await agent.run(prompt)
    code = result.output

    if not code.files:
        raise RuntimeError(
            "Developer agent returned empty files dict — no source code was generated. "
            "This usually indicates a structured-output failure with the chosen model. "
            f"Model: {state.model_for('developer')}"
        )

    # Remove infrastructure files that the Developer must not own:
    # - Dockerfile/compose: DevOps generates these; Developer's version often has a
    #   hardcoded flag literal that trips `flag_not_in_source`.
    # - flag.txt: should never be in code.files — the flag is injected at build time
    #   via --build-arg FLAG=... into /flag.txt inside the image.
    _devops_owned = {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "flag.txt"}
    for key in list(code.files.keys()):
        if key.lower() in _devops_owned:
            del code.files[key]

    # Strip solver-only packages from python_packages. The Developer sometimes lists
    # exploit tools (pwntools, angr, z3-solver) that belong in the solve script, not
    # the challenge image. Installing them bloats the image and can cause build timeouts.
    _solver_packages = {"pwntools", "angr", "z3-solver", "z3", "capstone", "unicorn", "keystone-engine"}
    if code.python_packages:
        code.python_packages = [
            p for p in code.python_packages if p.lower() not in _solver_packages
        ]

    state.code = code
    return state
