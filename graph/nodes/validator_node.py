"""Validator node — runs deterministic sandbox checks, then an LLM review for unintended bugs.

The deterministic layer (flag-in-source, docker build/run/solve) decides whether the
challenge actually works. The LLM layer adds qualitative judgement about extra attack
surface the sandbox can't see (information leaks, default credentials, etc.).
"""

from __future__ import annotations

import logging
import re

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


def _flag_matches_regex_check(state: CTFState) -> ValidationCheck | None:
    """Deterministic: if event.flag_regex is set, the generated flag must match it."""
    if state.event is None:
        return None
    if state.manifest is None:
        raise RuntimeError("Validator called before Architect")
    flag = state.manifest.flag
    regex = state.event.flag_regex
    if re.fullmatch(regex, flag):
        return ValidationCheck(check="flag_matches_regex", passed=True, detail="ok")
    return ValidationCheck(
        check="flag_matches_regex", passed=False,
        detail=f"flag {flag!r} does not match event regex {regex}",
    )


def _secret_key_placeholder_check(state: CTFState) -> ValidationCheck | None:
    """Deterministic: reject placeholder or hardcoded SECRET_KEY values."""
    assert state.code is not None
    placeholder_markers = ("your-secret-key-here", "placeholder", "changeme", "secret_key = ''")
    offenders: list[str] = []
    for filename, content in state.code.files.items():
        upper = content.upper()
        if "SECRET_KEY" not in upper:
            continue
        lower = content.lower()
        if any(marker in lower for marker in placeholder_markers):
            offenders.append(filename)
    if offenders:
        return ValidationCheck(
            check="secret_key_placeholder",
            passed=False,
            detail=f"placeholder or hardcoded SECRET_KEY found in files: {offenders}",
        )
    return ValidationCheck(check="secret_key_placeholder", passed=True, detail="ok")


def _masked_template_errors_check(state: CTFState) -> ValidationCheck | None:
    """Deterministic: flag blanket exception masking around template rendering paths."""
    assert state.code is not None
    suspicious_files: list[str] = []
    for filename, content in state.code.files.items():
        lower = content.lower()
        if "render_template_string(" not in lower:
            continue
        if "except exception" in lower and ("generic error message" in lower or "processing your request" in lower):
            suspicious_files.append(filename)
    if suspicious_files:
        return ValidationCheck(
            check="masked_template_errors",
            passed=False,
            detail=(
                "blanket exception handling masks template/rendering errors in files: "
                f"{suspicious_files}"
            ),
        )
    return ValidationCheck(check="masked_template_errors", passed=True, detail="ok")


def _decide_retry_target(checks: list[ValidationCheck]) -> RetryTarget:
    """Route retries to the smallest stage likely to fix the failing checks.

    Priority:
    1) Infra/build/runtime checks failing -> DevOps
    2) Only solver checks failing        -> Solver
    3) Anything else                     -> Developer
    """
    failed = [c.check for c in checks if not c.passed]
    if not failed:
        return RetryTarget.DEVELOPER

    infra_scope = {
        "docker_build",
        "docker_network_create",
        "container_start",
        "server_ready",
        "sandbox_available",
        "sandbox_run",
    }
    if any(name in infra_scope for name in failed):
        return RetryTarget.DEVOPS

    solver_scope = {"solver_run", "flag_captured"}
    if set(failed).issubset(solver_scope):
        return RetryTarget.SOLVER
    return RetryTarget.DEVELOPER


def _docker_build_retry_hints(check_detail: str) -> list[str]:
    """Extract actionable, deterministic hints from docker_build stderr snippets."""
    detail = check_detail.lower()
    hints: list[str] = []

    if "make: not found" in detail:
        hints.append(
            "Dockerfile build failed because 'make' is missing. Install it in apt-get "
            "(for example: `gcc libc6-dev make` or `build-essential`)."
        )
    if "fromplatformflagconstdisallowed" in detail or "--platform" in detail:
        hints.append(
            "Avoid hardcoding `FROM --platform=linux/amd64` in Dockerfile. Let platform "
            "be selected by runtime/build environment unless explicitly required by challenge constraints."
        )
    if "jsonargsrecommended" in detail:
        hints.append(
            "Use exec-form JSON CMD/ENTRYPOINT (for example: `CMD [\"./challenge\", \"1337\"]`) "
            "instead of shell form for stable signal handling."
        )

    return hints


async def _llm_review(state: CTFState, sandbox_output: str | None = None) -> ValidationResult:
    """Ask the LLM to look for unintended bugs the sandbox can't see.

    If `sandbox_output` is provided, include it (or a trimmed excerpt) in the
    prompt so the LLM can reason about runtime failures and log traces.
    """
    from agents.factory import create_agent

    assert state.manifest is not None and state.code is not None
    assert state.infra is not None and state.solver is not None

    agent = create_agent("validator", ValidationResult, model=state.model_for("validator"))
    event_info = ""
    if state.event is not None:
        event_info = (
            f"Event: {state.event.name}\n"
            f"Flag regex (all flags must match this): {state.event.flag_regex}\n"
            "NOTE: The flag format is defined by flag_regex above. Do NOT flag the "
            "flag prefix as wrong — e.g. if flag_regex requires 'OR{', then 'OR{...}' "
            "is correct. Only flag the flag format as wrong if it literally does not "
            "match the regex.\n\n"
        )
    solve_path = state.manifest.intended_solve_path.strip()
    contract_section = ""
    if solve_path:
        contract_section = (
            "## INTENDED_SOLVE_PATH CONTRACT (enforce strictly)\n"
            f"{solve_path}\n\n"
            "For each numbered step, verify (a) the Developer's code makes the step "
            "executable, and (b) the Solver's solve_script actually performs that step. "
            "Cite the step number in any error you raise about a contract violation "
            "(e.g. 'Step 2 violated: ...').\n\n"
        )
    prompt = (
        "Review this challenge for (1) intended_solve_path contract violations, "
        "(2) unintended bugs (default credentials, extra injection points, info "
        "leaks, missing auth), and (3) qualitative issues. The sandbox already "
        "verified build/run/solve mechanics — focus on what the sandbox can't see.\n\n"
        f"{event_info}"
        f"{contract_section}"
        f"Manifest:\n{state.manifest.model_dump_json(indent=2)}\n\n"
        f"Story:\n{state.story.model_dump_json(indent=2) if state.story else 'N/A'}\n\n"
        f"Code:\n{state.code.model_dump_json(indent=2)}\n\n"
        f"Infra:\n{state.infra.model_dump_json(indent=2)}\n\n"
        f"Solver:\n{state.solver.model_dump_json(indent=2)}"
    )
    # Include a trimmed sandbox output if available — avoid flooding the prompt.
    if sandbox_output:
        excerpt = sandbox_output[-8000:]
        prompt += f"\n\nSandbox runtime output (truncated to last 8k chars):\n{excerpt}"
    result = await agent.run(prompt)
    return result.output


async def run(state: CTFState) -> CTFState:
    """Run the Validator: deterministic checks first, then LLM review."""
    _require_pipeline_outputs(state)

    checks: list[ValidationCheck] = [_flag_in_source_check(state)]
    secret_check = _secret_key_placeholder_check(state)
    if secret_check is not None:
        checks.append(secret_check)
    masked_error_check = _masked_template_errors_check(state)
    if masked_error_check is not None:
        checks.append(masked_error_check)
    regex_check = _flag_matches_regex_check(state)
    if regex_check is not None:
        checks.append(regex_check)
    flag_captured = False
    errors: list[str] = []

    docker_unavailable_reason = (
        DockerSandbox.availability_error() if state.use_sandbox else None
    )
    if state.use_sandbox and docker_unavailable_reason is None:
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
            detail=f"Docker not available: {docker_unavailable_reason}",
        ))
        errors.append(
            f"Docker unavailable ({docker_unavailable_reason}); "
            "deterministic verification skipped"
        )
    else:
        checks.append(ValidationCheck(
            check="sandbox_available", passed=True,
            detail="sandbox disabled by config (use_sandbox=False)",
        ))

    deterministic_passed = all(c.passed for c in checks)

    # Provide the sandbox output to the LLM review so it can reference runtime logs.
    sandbox_output = output if state.use_sandbox and 'output' in locals() else None
    llm_result = await _llm_review(state, sandbox_output)

    combined_passed = deterministic_passed and llm_result.passed and (
        flag_captured or not state.use_sandbox
    )
    combined_checks = checks + list(llm_result.checks)
    retry_target = _decide_retry_target(combined_checks)

    # Prepend deterministic failures to retry_instructions so the developer
    # always sees concrete, specific fixes — not just LLM commentary.
    assert state.manifest is not None  # narrowed above
    deterministic_failures = [c for c in checks if not c.passed]
    det_prefix = ""
    if deterministic_failures:
        lines = ["DETERMINISTIC CHECK FAILURES (fix these first):"]
        for c in deterministic_failures:
            lines.append(f"  - {c.check}: {c.detail}")
        docker_build_failure = next((c for c in deterministic_failures if c.check == "docker_build"), None)
        if docker_build_failure is not None:
            hints = _docker_build_retry_hints(docker_build_failure.detail)
            if hints:
                lines.append("\nCRITICAL: Docker build fix hints:")
                lines.extend(f"  - {hint}" for hint in hints)
        # Add explicit flag-in-source guidance when that specific check fails.
        if any(c.check == "flag_not_in_source" for c in deterministic_failures):
            flag = state.manifest.flag
            lines.append(
                f"\nCRITICAL: The flag literal {flag!r} must NEVER appear in source code. "
                "Read it exclusively from '/flag.txt' at runtime — no fallback, no default:\n"
                "    with open('/flag.txt') as f:\n"
                "        flag = f.read().strip()\n"
                "Remove every inline occurrence of the flag string, including fallback "
                "values like `flag = 'OR{...}'` or `flag_content = 'OR{...}'`."
            )
        if any(c.check == "secret_key_placeholder" for c in deterministic_failures):
            lines.append(
                "\nCRITICAL: Do not hardcode SECRET_KEY or other placeholder secrets. "
                "Read secrets from the environment (for example, os.environ.get('SECRET_KEY')) "
                "or generate them at runtime if the challenge does not require persistence."
            )
        if any(c.check == "masked_template_errors" for c in deterministic_failures):
            lines.append(
                "\nCRITICAL: Do not hide template/rendering failures behind a blanket "
                "exception handler. The solver must be able to observe the rendered output "
                "and diagnose the intended vulnerability."
            )
        det_prefix = "\n".join(lines) + "\n\n"

    retry_instructions = det_prefix + (llm_result.retry_instructions or "")

    combined_errors = errors + list(llm_result.errors)

    # The LLM occasionally returns `passed=False` with empty errors/retry_instructions,
    # leaving the retry loop blind. Backfill from concrete signals so the Developer
    # always sees something actionable on the next attempt.
    if not combined_passed and not combined_errors:
        failed_checks = [c for c in combined_checks if not c.passed]
        if failed_checks:
            combined_errors = [f"{c.check}: {c.detail}" for c in failed_checks]
        else:
            combined_errors = [
                "Validator returned passed=False but provided no error detail. "
                "Treating as ambiguous failure; re-running developer."
            ]
    if not combined_passed and not retry_instructions.strip():
        retry_instructions = "Previous attempt failed validation:\n" + "\n".join(
            f"  - {e}" for e in combined_errors
        )

    state.validation = ValidationResult(
        passed=combined_passed,
        flag_captured=flag_captured,
        checks=combined_checks,
        errors=combined_errors,
        suggestions=list(llm_result.suggestions),
        retry_instructions=retry_instructions,
        sandbox_output=sandbox_output or llm_result.sandbox_output,
        retry_target=retry_target,
    )
    return state
