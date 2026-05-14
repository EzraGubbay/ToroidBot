"""Validator node — runs deterministic sandbox checks, then an LLM review for unintended bugs.

The deterministic layer (flag-in-source, docker build/run/solve) decides whether the
challenge actually works. The LLM layer adds qualitative judgement about extra attack
surface the sandbox can't see (information leaks, default credentials, etc.).
"""

from __future__ import annotations

import logging

from agents.factory import create_agent
from agents.schemas import (
    CTFState,
    RetryTarget,
    ValidationCheck,
    ValidationResult,
)
from sandbox.docker_runtime import DockerSandbox

log = logging.getLogger(__name__)


def _require_pipeline_outputs(state: CTFState) -> None:
    missing = [
        name for name, value in (
            ("manifest", state.manifest),
            ("code", state.code),
            ("infra", state.infra),
            ("solver", state.solver),
        ) if value is None
    ]
    if missing:
        raise RuntimeError(f"Validator missing required pipeline outputs: {missing}")


def _flag_in_source_check(state: CTFState) -> ValidationCheck:
    """Deterministic: flag must not appear in any player-readable source file."""
    assert state.manifest is not None and state.code is not None  # narrowed by caller
    flag = state.manifest.flag
    leaked: list[str] = [
        filename for filename, content in state.code.files.items()
        if flag in content
    ]
    if leaked:
        return ValidationCheck(
            check="flag_not_in_source", passed=False,
            detail=f"flag {flag!r} appears in player-readable files: {leaked}",
        )
    return ValidationCheck(check="flag_not_in_source", passed=True, detail="ok")


def _decide_retry_target(checks: list[ValidationCheck]) -> RetryTarget:
    """If the only failures are in solver_run/flag_captured, blame the Solver; else the Developer."""
    failed = [c.check for c in checks if not c.passed]
    if not failed:
        return RetryTarget.DEVELOPER
    solver_scope = {"solver_run", "flag_captured"}
    if set(failed).issubset(solver_scope):
        return RetryTarget.SOLVER
    return RetryTarget.DEVELOPER


async def _llm_review(state: CTFState) -> ValidationResult:
    """Ask the LLM to look for unintended bugs the sandbox can't see."""
    assert state.manifest is not None and state.code is not None
    assert state.infra is not None and state.solver is not None

    agent = create_agent("validator", ValidationResult, model=state.model)
    prompt = (
        "Review this challenge for unintended bugs (default credentials, extra "
        "injection points, info leaks, missing auth) and qualitative issues. "
        "The sandbox already verified build/run/solve mechanics — focus on what "
        "the sandbox can't see.\n\n"
        f"Manifest:\n{state.manifest.model_dump_json(indent=2)}\n\n"
        f"Story:\n{state.story.model_dump_json(indent=2) if state.story else 'N/A'}\n\n"
        f"Code:\n{state.code.model_dump_json(indent=2)}\n\n"
        f"Infra:\n{state.infra.model_dump_json(indent=2)}\n\n"
        f"Solver:\n{state.solver.model_dump_json(indent=2)}"
    )
    result = await agent.run(prompt)
    return result.output


async def run(state: CTFState) -> CTFState:
    """Run the Validator: deterministic checks first, then LLM review."""
    _require_pipeline_outputs(state)

    checks: list[ValidationCheck] = [_flag_in_source_check(state)]
    flag_captured = False
    errors: list[str] = []

    if state.use_sandbox and DockerSandbox.available():
        try:
            with DockerSandbox(state) as sandbox:
                sandbox_checks, flag_captured, output = sandbox.verify()
            checks.extend(sandbox_checks)
            if not flag_captured:
                errors.append("Sandbox did not capture the expected flag")
                log.debug("Sandbox output:\n%s", output)
        except Exception as e:
            checks.append(ValidationCheck(
                check="sandbox_run", passed=False, detail=f"sandbox raised: {e}",
            ))
            errors.append(f"Sandbox exception: {e}")
    elif state.use_sandbox:
        checks.append(ValidationCheck(
            check="sandbox_available", passed=False,
            detail="Docker not available — skipping deterministic build/solve checks",
        ))
        errors.append("Docker unavailable; deterministic verification skipped")
    else:
        checks.append(ValidationCheck(
            check="sandbox_available", passed=True,
            detail="sandbox disabled by config (use_sandbox=False)",
        ))

    deterministic_passed = all(c.passed for c in checks)

    llm_result = await _llm_review(state)

    combined_passed = deterministic_passed and llm_result.passed and (
        flag_captured or not state.use_sandbox
    )
    combined_checks = checks + list(llm_result.checks)
    retry_target = _decide_retry_target(combined_checks)

    state.validation = ValidationResult(
        passed=combined_passed,
        flag_captured=flag_captured,
        checks=combined_checks,
        errors=errors + list(llm_result.errors),
        suggestions=list(llm_result.suggestions),
        retry_instructions=llm_result.retry_instructions,
        retry_target=retry_target,
    )
    return state
