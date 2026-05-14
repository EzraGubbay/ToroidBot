"""Shared fixtures and path setup so tests can import project packages."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import pytest  # noqa: E402

from agents.schemas import (  # noqa: E402
    Category,
    ChallengeCode,
    ChallengeInfra,
    ChallengeManifest,
    ChallengeSolver,
    ChallengeStory,
    CTFState,
)


@pytest.fixture
def manifest() -> ChallengeManifest:
    return ChallengeManifest(
        name="sample-web-1",
        category=Category.WEB,
        difficulty=2,
        vulnerability="SQL injection in login form",
        language="python",
        services=["web server"],
        tools_required=["requests"],
        flag="CTF{test-flag-xyz}",
    )


@pytest.fixture
def story() -> ChallengeStory:
    return ChallengeStory(
        title="Sample",
        description="A sample challenge.",
        hints=["hint1", "hint2"],
        theme="cyberpunk",
    )


@pytest.fixture
def code() -> ChallengeCode:
    return ChallengeCode(
        files={"app.py": "print('hello')\n"},
        entry_point="app.py",
        flag_location="/flag.txt",
        intended_vulnerability="app.py:login() — unsanitized SQL",
    )


@pytest.fixture
def code_with_flag_leak() -> ChallengeCode:
    return ChallengeCode(
        files={
            "app.py": "FLAG = 'CTF{test-flag-xyz}'\n",
            "README.md": "no leak here",
        },
        entry_point="app.py",
        flag_location="/flag.txt",
        intended_vulnerability="app.py:login() — unsanitized SQL",
    )


@pytest.fixture
def infra() -> ChallengeInfra:
    return ChallengeInfra(
        dockerfile="FROM python:3.12-slim\nCMD python app.py\n",
        exposed_ports=[1337],
        startup_command="python app.py",
    )


@pytest.fixture
def solver() -> ChallengeSolver:
    return ChallengeSolver(
        solve_script="print('CTF{test-flag-xyz}')\n",
        dependencies=["requests"],
        expected_flag="CTF{test-flag-xyz}",
        solve_steps=["connect", "exploit", "read flag"],
    )


@pytest.fixture
def state(manifest, story, code, infra, solver) -> CTFState:
    return CTFState(
        user_prompt="test",
        manifest=manifest,
        story=story,
        code=code,
        infra=infra,
        solver=solver,
    )
