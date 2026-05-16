"""Entry point for the CTF challenge generator."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agents.event_config import load_event_config
from agents.schemas import CTFState, ValidationCheck
from orchestrator.budget import BudgetExhaustedError, fetch_balance, guard_budget, log_run
from orchestrator.output import save_challenge

_DEFAULT_MAX_RETRIES = 3
_SENTINEL_MODEL = "__cli_default_model_unset__"


def _print_validation_failure_summary(state: CTFState) -> None:
    """Emit the full validator feedback so failures are actionable."""
    if state.validation is None:
        return

    print("Validation summary:", file=sys.stderr)
    print(f"  passed: {state.validation.passed}", file=sys.stderr)
    print(f"  retry_target: {state.validation.retry_target.value}", file=sys.stderr)
    print(f"  flag_captured: {state.validation.flag_captured}", file=sys.stderr)

    if state.validation.errors:
        print("  errors:", file=sys.stderr)
        for error in state.validation.errors:
            print(f"    - {error}", file=sys.stderr)

    failing_checks = [check for check in state.validation.checks if not check.passed]
    if failing_checks:
        print("  failing checks:", file=sys.stderr)
        for check in failing_checks:
            print(f"    - {check.check}: {check.detail}", file=sys.stderr)

    if state.validation.retry_instructions.strip():
        print("  retry instructions:", file=sys.stderr)
        for line in state.validation.retry_instructions.rstrip().splitlines():
            print(f"    {line}", file=sys.stderr)

    if state.failed_solver_scripts:
        print(
            f"  failed solver scripts retained: {len(state.failed_solver_scripts)}",
            file=sys.stderr,
        )
    if state.validation.sandbox_output:
        print("\n  --- sandbox output (last 1k chars) ---", file=sys.stderr)
        print(state.validation.sandbox_output[-1000:], file=sys.stderr)


def parse_args_from(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a CTF challenge from a natural language prompt.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help='Challenge description (e.g., "Create a medium web challenge about SQL injection")',
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to event config (YAML or JSON). Optional.",
    )
    parser.add_argument(
        "--model",
        default=_SENTINEL_MODEL,
        help="Model string in <provider>:<model> format. Overrides config when set.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Validation retry attempts after the first run. Overrides config when set.",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Skip Docker sandbox in the Validator. Overrides config when set.",
    )
    parser.add_argument(
        "--save-state",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "After the LLM pipeline completes, write the full CTFState JSON to PATH. "
            "Use with --load-state to replay sandbox checks without re-running LLMs."
        ),
    )
    parser.add_argument(
        "--load-state",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Skip the LLM pipeline. Load CTFState from PATH (written by --save-state) "
            "and run only the deterministic sandbox checks. Free — no API calls."
        ),
    )
    args = parser.parse_args(argv)
    if args.load_state is None and args.prompt is None:
        parser.error("prompt is required unless --load-state is used")
    return args


def parse_args() -> argparse.Namespace:
    return parse_args_from(sys.argv[1:])


def build_state(args: argparse.Namespace) -> CTFState:
    """Combine CLI args + optional event config into a CTFState.

    Precedence: CLI flag > event config > built-in defaults.
    """
    event = load_event_config(args.config) if args.config else None

    state = CTFState(user_prompt=args.prompt, event=event)

    # max_retries: CLI > config > built-in
    if args.max_retries is not None:
        state.max_retries = args.max_retries
    elif event is not None:
        state.max_retries = event.max_retries
    else:
        state.max_retries = _DEFAULT_MAX_RETRIES

    # use_sandbox: --no-sandbox is the only CLI override (it can only force False).
    # If --no-sandbox is set, state.use_sandbox=False regardless of config.
    if args.no_sandbox:
        state.use_sandbox = False
    elif event is not None:
        state.use_sandbox = event.use_sandbox

    # CLI --model is the global override; only set when explicitly passed.
    if args.model != _SENTINEL_MODEL:
        state.set_cli_model_override(args.model)

    return state


def _run_sandbox_replay(state: CTFState) -> None:
    """Run deterministic sandbox checks against a previously saved state.

    No LLM calls. Exits 0 on flag capture, 1 on failure.
    """
    from sandbox.docker_runtime import DockerSandbox

    if not DockerSandbox.available():
        print("Docker is not available — cannot run sandbox replay.", file=sys.stderr)
        sys.exit(1)

    missing = [f for f in ("manifest", "code", "infra", "solver") if getattr(state, f) is None]
    if missing:
        print(f"Loaded state is missing: {missing}. Was it saved after the full pipeline?", file=sys.stderr)
        sys.exit(1)

    print(f"[replay] challenge: {state.manifest.name}")  # type: ignore[union-attr]
    print(f"[replay] flag:      {state.manifest.flag}")  # type: ignore[union-attr]
    print()

    with DockerSandbox(state) as sandbox:
        checks, flag_captured, output = sandbox.verify()

    passed = [c for c in checks if c.passed]
    failed = [c for c in checks if not c.passed]

    for c in passed:
        print(f"  [pass] {c.check}")
    for c in failed:
        print(f"  [FAIL] {c.check}: {c.detail}", file=sys.stderr)

    if flag_captured:
        print(f"\n[replay] Flag captured!")
        sys.exit(0)
    else:
        print(f"\n[replay] Flag NOT captured.", file=sys.stderr)
        sys.exit(1)


async def async_main() -> None:
    load_dotenv()
    args = parse_args()

    # ── Sandbox replay mode (--load-state) ────────────────────────────────
    if args.load_state:
        path = args.load_state
        if not path.exists():
            print(f"State file not found: {path}", file=sys.stderr)
            sys.exit(1)
        state = CTFState.model_validate_json(path.read_text(encoding="utf-8"))
        print(f"Loaded state from {path}")
        _run_sandbox_replay(state)
        return  # _run_sandbox_replay calls sys.exit

    # ── Normal pipeline mode ───────────────────────────────────────────────
    from graph.pipeline import run_pipeline

    state = build_state(args)

    print(f"Generating challenge: {args.prompt}")
    if state.event:
        print(f"Event: {state.event.name}")
    print()

    # Track OpenRouter spend when the API key is present.
    _track_budget = bool(os.environ.get("OPENROUTER_API_KEY"))
    _used_before: float = 0.0
    _limit: float | None = None
    if _track_budget:
        try:
            _used_before, _limit = await guard_budget()
            _remaining = (_limit - _used_before) if _limit is not None else None
            _bal_str = f"${_remaining:.2f} remaining" if _remaining is not None else "no limit"
            print(f"[budget] OpenRouter balance: {_bal_str}", file=sys.stderr, flush=True)
        except BudgetExhaustedError as exc:
            print(f"[budget] HARD STOP — {exc}", file=sys.stderr)
            sys.exit(2)

    state = await run_pipeline(state)

    # Save pipeline state before validation verdict (if requested).
    if args.save_state:
        args.save_state.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        print(f"[state] Pipeline state saved to {args.save_state}", file=sys.stderr, flush=True)

    if _track_budget:
        try:
            _used_after, _ = await fetch_balance()
            log_path = log_run(
                prompt=args.prompt,
                event_name=state.event.name if state.event else None,
                used_before=_used_before,
                used_after=_used_after,
                limit=_limit,
            )
            _cost = _used_after - _used_before
            _remaining_after = (_limit - _used_after) if _limit is not None else None
            _rem_str = f"  ${_remaining_after:.2f} remaining" if _remaining_after is not None else ""
            print(
                f"[budget] Run cost: ${_cost:.4f}{_rem_str}  (log: {log_path})",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[budget] Could not record usage: {exc}", file=sys.stderr)

    if state.validation and state.validation.passed:
        output_dir = save_challenge(state)
        print(f"\nChallenge saved to: {output_dir}")
    else:
        print("\nChallenge generation failed after validation.", file=sys.stderr)
        _print_validation_failure_summary(state)
        sys.exit(1)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
