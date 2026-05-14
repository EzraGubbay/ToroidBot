"""Entry point for the CTF challenge generator."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from agents.event_config import load_event_config
from agents.schemas import CTFState
from graph.pipeline import run_pipeline
from orchestrator.output import save_challenge

_DEFAULT_MAX_RETRIES = 3
_SENTINEL_MODEL = "__cli_default_model_unset__"


def parse_args_from(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a CTF challenge from a natural language prompt.",
    )
    parser.add_argument(
        "prompt",
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
    return parser.parse_args(argv)


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


async def async_main() -> None:
    load_dotenv()
    args = parse_args()
    state = build_state(args)

    print(f"Generating challenge: {args.prompt}")
    if state.event:
        print(f"Event: {state.event.name}")
    print()

    state = await run_pipeline(state)

    if state.validation and state.validation.passed:
        output_dir = save_challenge(state)
        print(f"\nChallenge saved to: {output_dir}")
    else:
        print("\nChallenge generation failed after validation.", file=sys.stderr)
        if state.validation:
            for error in state.validation.errors:
                print(f"  - {error}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
