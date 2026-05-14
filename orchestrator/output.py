"""Saves generated challenge files to the output directory."""

from __future__ import annotations

import json
from pathlib import Path

from agents.schemas import CTFState

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def save_challenge(state: CTFState) -> Path:
    """Write all generated files to output/<challenge_name>/.

    Args:
        state: Completed pipeline state with all agent outputs.

    Returns:
        Path to the output directory.
    """
    assert state.manifest is not None
    assert state.code is not None
    assert state.infra is not None
    assert state.solver is not None

    challenge_dir = OUTPUT_DIR / state.manifest.name
    challenge_dir.mkdir(parents=True, exist_ok=True)

    # Write challenge source files
    for filename, content in state.code.files.items():
        file_path = challenge_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

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
