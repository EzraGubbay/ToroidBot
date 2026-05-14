"""Entry point for the CTF challenge generator."""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from agents.schemas import CTFState
from graph.pipeline import run_pipeline
from orchestrator.output import save_challenge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a CTF challenge from a natural language prompt.",
    )
    parser.add_argument(
        "prompt",
        help='Challenge description (e.g., "Create a medium web challenge about SQL injection")',
    )
    parser.add_argument(
        "--model",
        default="google-gla:gemini-2.5-flash",
        help="Model string in <provider>:<model> format (default: google-gla:gemini-2.5-flash)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max validation retry attempts (default: 3)",
    )
    return parser.parse_args()


async def async_main() -> None:
    load_dotenv()
    args = parse_args()

    state = CTFState(
        user_prompt=args.prompt,
        model=args.model,
        max_retries=args.max_retries,
    )

    print(f"Generating challenge: {args.prompt}")
    print(f"Model: {args.model}")
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
