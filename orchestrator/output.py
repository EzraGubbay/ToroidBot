"""Saves generated challenge files to the output directory."""

from __future__ import annotations

import json
from pathlib import Path

from agents.event_config import slugify_event_name
from agents.schemas import CTFState

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def save_challenge(state: CTFState) -> Path:
    """Write all generated files to output/<challenge_name>/.

    Args:
        state: Completed pipeline state with all agent outputs.

    Returns:
        Path to the output directory.
    """
    missing = [
        name for name, value in (
            ("manifest", state.manifest),
            ("code", state.code),
            ("infra", state.infra),
            ("solver", state.solver),
        ) if value is None
    ]
    if missing:
        raise RuntimeError(f"save_challenge missing required pipeline outputs: {missing}")

    if state.event is not None:
        base = OUTPUT_DIR / slugify_event_name(state.event.name)
    else:
        base = OUTPUT_DIR
    challenge_dir = base / state.manifest.name
    challenge_dir.mkdir(parents=True, exist_ok=True)

    # Write challenge source files
    for filename, content in state.code.files.items():
        file_path = challenge_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # Write flag.txt so Dockerfiles that COPY flag.txt /flag.txt work out of the box
    (challenge_dir / "flag.txt").write_text(state.manifest.flag, encoding="utf-8")

    # Write Dockerfile
    (challenge_dir / "Dockerfile").write_text(state.infra.dockerfile, encoding="utf-8")

    # Write docker-compose.yml if present
    if state.infra.compose_file:
        (challenge_dir / "docker-compose.yml").write_text(
            state.infra.compose_file, encoding="utf-8"
        )

    # Write solve script
    ext = {"python": ".py", "bash": ".sh"}.get(state.solver.solve_language, ".py")
    (challenge_dir / f"solve{ext}").write_text(state.solver.solve_script, encoding="utf-8")

    # Write challenge README with story
    if state.story:
        readme = f"# {state.story.title}\n\n{state.story.description}\n"
        if state.story.hints:
            readme += "\n## Hints\n\n"
            for i, hint in enumerate(state.story.hints, 1):
                readme += f"{i}. {hint}\n"
        (challenge_dir / "README.md").write_text(readme, encoding="utf-8")

    # Write full state as metadata
    (challenge_dir / "challenge_meta.json").write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )

    return challenge_dir
