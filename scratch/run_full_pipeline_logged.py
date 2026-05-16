"""Run the full challenge pipeline with file-backed progress tracking.

This script is meant for interactive experiments where you want:
- a single input prompt (for example, a web challenge with a flag hidden in HTML)
- the full Architect → Storyteller → Developer → DevOps → Solver → Validator flow
- a persistent run directory with logs and per-stage state snapshots

Usage:

    uv run python scratch/run_full_pipeline_logged.py
    uv run python scratch/run_full_pipeline_logged.py "Create a web challenge where the flag is hidden in the HTML source"
    uv run python scratch/run_full_pipeline_logged.py --config examples/configs/megactf-2026.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.event_config import load_event_config  # noqa: E402
from agents.schemas import CTFState, RetryTarget  # noqa: E402
from orchestrator.budget import BudgetExhaustedError, fetch_balance, guard_budget, log_run  # noqa: E402
from orchestrator.output import save_challenge  # noqa: E402

_DEFAULT_PROMPT = "Create an easy web challenge where the flag is hidden in the HTML source comments."
_DEFAULT_MAX_RETRIES = 3
_SENTINEL_MODEL = "__cli_default_model_unset__"


@dataclass
class RunArtifacts:
    run_dir: Path
    log_path: Path
    checkpoints_dir: Path
    final_state_path: Path
    metadata_path: Path


class _TeeStream:
    def __init__(self, *streams: Any):
        self._streams = streams

    def write(self, text: str) -> int:
        written = 0
        for stream in self._streams:
            written = stream.write(text)
            stream.flush()
        return written

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def _print_validation_failure_summary(state: CTFState) -> None:
    """Emit the full validator feedback so failures are actionable."""
    if state.validation is None:
        return

    print("Validation summary:", file=sys.stderr, flush=True)
    print(f"  passed: {state.validation.passed}", file=sys.stderr, flush=True)
    print(f"  retry_target: {state.validation.retry_target.value}", file=sys.stderr, flush=True)
    print(f"  flag_captured: {state.validation.flag_captured}", file=sys.stderr, flush=True)

    if state.validation.errors:
        print("  errors:", file=sys.stderr, flush=True)
        for error in state.validation.errors:
            print(f"    - {error}", file=sys.stderr, flush=True)

    failing_checks = [check for check in state.validation.checks if not check.passed]
    if failing_checks:
        print("  failing checks:", file=sys.stderr, flush=True)
        for check in failing_checks:
            print(f"    - {check.check}: {check.detail}", file=sys.stderr, flush=True)

    if state.validation.retry_instructions.strip():
        print("  retry instructions:", file=sys.stderr, flush=True)
        for line in state.validation.retry_instructions.rstrip().splitlines():
            print(f"    {line}", file=sys.stderr, flush=True)

    if state.failed_solver_scripts:
        print(
            f"  failed solver scripts retained: {len(state.failed_solver_scripts)}",
            file=sys.stderr,
            flush=True,
        )
    if state.validation.sandbox_output:
        print("\n  --- sandbox output (last 1k chars) ---", file=sys.stderr, flush=True)
        print(state.validation.sandbox_output[-1000:], file=sys.stderr, flush=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full ToroidBot pipeline with checkpointed progress logs.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=_DEFAULT_PROMPT,
        help="Challenge prompt to feed into the pipeline.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional event config (YAML or JSON).",
    )
    parser.add_argument(
        "--model",
        default=_SENTINEL_MODEL,
        help="Optional global model override in <provider>:<model> format.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Override validation retry attempts after the first run.",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Skip Docker sandbox checks in the Validator.",
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=PROJECT_ROOT / "scratch" / "pipeline-runs",
        help="Directory where logs and checkpoints will be written.",
    )
    return parser.parse_args(argv)


def _build_state(args: argparse.Namespace) -> CTFState:
    event = load_event_config(args.config) if args.config else None
    state = CTFState(user_prompt=args.prompt, event=event)

    if args.max_retries is not None:
        state.max_retries = args.max_retries
    elif event is not None:
        state.max_retries = event.max_retries
    else:
        state.max_retries = _DEFAULT_MAX_RETRIES

    if args.no_sandbox:
        state.use_sandbox = False
    elif event is not None:
        state.use_sandbox = event.use_sandbox

    if args.model != _SENTINEL_MODEL:
        state.set_cli_model_override(args.model)

    return state


def _create_run_artifacts(run_root: Path) -> RunArtifacts:
    run_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = run_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir = run_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    return RunArtifacts(
        run_dir=run_dir,
        log_path=run_dir / "run.log",
        checkpoints_dir=checkpoints_dir,
        final_state_path=run_dir / "final_state.json",
        metadata_path=run_dir / "run_metadata.json",
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _save_checkpoint(artifacts: RunArtifacts, name: str, state: CTFState) -> None:
    snapshot = artifacts.checkpoints_dir / name
    snapshot.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def _save_error_snapshot(artifacts: RunArtifacts, stage: str, exc: BaseException) -> Path:
    payload = {
        "stage": stage,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback.format_exc(),
        "stopped_at": datetime.now(timezone.utc).isoformat(),
    }
    path = artifacts.run_dir / "error.json"
    _write_json(path, payload)
    return path


async def _run_step(name: str, node, state: CTFState) -> CTFState:
    print(f"[{name}] starting…", file=sys.stderr, flush=True)
    started = datetime.now(timezone.utc)
    result = await node.run(state)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"[{name}] done in {elapsed:.1f}s", file=sys.stderr, flush=True)
    return result


async def _run_pipeline_with_checkpoints(state: CTFState, artifacts: RunArtifacts) -> CTFState:
    from graph.nodes import (  # noqa: WPS433
        architect_node,
        developer_node,
        devops_node,
        solver_node,
        storyteller_node,
        validator_node,
    )

    _save_checkpoint(artifacts, "00-initial.json", state)

    try:
        state = await _run_step("architect", architect_node, state)
        _save_checkpoint(artifacts, "01-architect.json", state)

        state = await _run_step("storyteller", storyteller_node, state)
        _save_checkpoint(artifacts, "02-storyteller.json", state)

        state = await _run_step("developer", developer_node, state)
        _save_checkpoint(artifacts, "03-developer.json", state)

        state = await _run_step("devops", devops_node, state)
        _save_checkpoint(artifacts, "04-devops.json", state)

        state = await _run_step("solver", solver_node, state)
        _save_checkpoint(artifacts, "05-solver.json", state)

        state = await _run_step("validator", validator_node, state)
        _save_checkpoint(artifacts, "06-validator.json", state)

        while not (state.validation and state.validation.passed):
            if state.retry_count >= state.max_retries:
                break

            # Persist the failing solver script before it is potentially
            # overwritten by the next Solver run.
            if state.solver is not None and state.validation and not state.validation.passed:
                failed_script = state.solver.solve_script
                if failed_script and (
                    not state.failed_solver_scripts
                    or state.failed_solver_scripts[-1] != failed_script
                ):
                    state.failed_solver_scripts.append(failed_script)

            state.retry_count += 1
            retry_target = state.validation.retry_target if state.validation else RetryTarget.DEVELOPER
            print(
                f"[pipeline] retry {state.retry_count}/{state.max_retries} (target={retry_target.value})",
                file=sys.stderr,
                flush=True,
            )

            if retry_target == RetryTarget.SOLVER:
                state = await _run_step("solver", solver_node, state)
                _save_checkpoint(artifacts, f"retry-{state.retry_count:02d}-solver.json", state)
            else:
                state = await _run_step("developer", developer_node, state)
                _save_checkpoint(artifacts, f"retry-{state.retry_count:02d}-developer.json", state)
                state = await _run_step("devops", devops_node, state)
                _save_checkpoint(artifacts, f"retry-{state.retry_count:02d}-devops.json", state)
                state = await _run_step("solver", solver_node, state)
                _save_checkpoint(artifacts, f"retry-{state.retry_count:02d}-solver.json", state)

            state = await _run_step("validator", validator_node, state)
            _save_checkpoint(artifacts, f"retry-{state.retry_count:02d}-validator.json", state)

    except Exception:
        # Re-raise after the caller stores an error snapshot and exits.
        raise

    return state


async def _async_main() -> int:
    load_dotenv()
    args = _parse_args(sys.argv[1:])
    state = _build_state(args)
    artifacts = _create_run_artifacts(args.run_root)

    metadata = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "prompt": args.prompt,
        "config": str(args.config) if args.config else None,
        "run_dir": str(artifacts.run_dir),
        "python": sys.version,
        "use_sandbox": state.use_sandbox,
        "max_retries": state.max_retries,
        "model": state.model,
        "event": state.event.model_dump() if state.event else None,
    }
    _write_json(artifacts.metadata_path, metadata)

    stdout_log = artifacts.run_dir / "stdout.log"
    stderr_log = artifacts.run_dir / "stderr.log"
    with stdout_log.open("w", encoding="utf-8") as stdout_handle, stderr_log.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        tee_stdout = _TeeStream(sys.stdout, stdout_handle)
        tee_stderr = _TeeStream(sys.stderr, stderr_handle)

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            print(f"Run directory: {artifacts.run_dir}")
            print(f"Prompt: {args.prompt}")
            if state.event:
                print(f"Event: {state.event.name}")
            print()

            track_budget = bool(os.environ.get("OPENROUTER_API_KEY"))
            used_before = 0.0
            limit = None
            if track_budget:
                try:
                    used_before, limit = await guard_budget()
                    remaining = (limit - used_before) if limit is not None else None
                    remaining_text = f"${remaining:.2f} remaining" if remaining is not None else "no limit"
                    print(f"[budget] OpenRouter balance: {remaining_text}", file=sys.stderr, flush=True)
                except BudgetExhaustedError as exc:
                    print(f"[budget] HARD STOP — {exc}", file=sys.stderr, flush=True)
                    return 2

            try:
                state = await _run_pipeline_with_checkpoints(state, artifacts)
            except Exception as exc:
                error_path = _save_error_snapshot(artifacts, "pipeline", exc)
                print(f"[error] Aborting pipeline on first failure: {exc}", file=sys.stderr, flush=True)
                print(f"[error] Saved details to: {error_path}", file=sys.stderr, flush=True)
                return 1

            _write_json(artifacts.final_state_path, json.loads(state.model_dump_json(indent=2)))

            if track_budget:
                try:
                    used_after, _ = await fetch_balance()
                    log_path = log_run(
                        prompt=args.prompt,
                        event_name=state.event.name if state.event else None,
                        used_before=used_before,
                        used_after=used_after,
                        limit=limit,
                    )
                    cost = used_after - used_before
                    remaining_after = (limit - used_after) if limit is not None else None
                    remaining_text = (
                        f"  ${remaining_after:.2f} remaining" if remaining_after is not None else ""
                    )
                    print(
                        f"[budget] Run cost: ${cost:.4f}{remaining_text}  (log: {log_path})",
                        file=sys.stderr,
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[budget] Could not record usage: {exc}", file=sys.stderr, flush=True)

            if state.validation and state.validation.passed:
                output_dir = save_challenge(state)
                print()
                print(f"Challenge saved to: {output_dir}")
                print(f"Run log: {artifacts.log_path}")
                print(f"State snapshots: {artifacts.checkpoints_dir}")
                return 0

            print("\nChallenge generation failed after validation.", file=sys.stderr, flush=True)
            _print_validation_failure_summary(state)
            print(f"Run log: {artifacts.log_path}", file=sys.stderr, flush=True)
            print(f"State snapshots: {artifacts.checkpoints_dir}", file=sys.stderr, flush=True)
            return 1


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()