"""Solver prompt composition for retry/debug context."""

from __future__ import annotations

from agents.schemas import (
    Category,
    ChallengeCode,
    ChallengeInfra,
    ChallengeManifest,
    ChallengeSolver,
    CTFState,
    ValidationCheck,
    ValidationResult,
)
from graph.nodes.solver_node import _build_solver_prompt


def _state() -> CTFState:
    return CTFState(
        user_prompt="x",
        manifest=ChallengeManifest(
            name="test-1",
            category=Category.WEB,
            difficulty=2,
            vulnerability="source disclosure",
            language="python",
            services=["web server"],
            tools_required=["curl"],
            flag="CTF{abcdefgh}",
            intended_solve_path="1) GET / 2) find flag",
        ),
        code=ChallengeCode(
            files={"app.py": "print('x')\n"},
            entry_point="app.py",
            flag_location="/flag.txt",
            intended_vulnerability="x",
        ),
        infra=ChallengeInfra(
            dockerfile="FROM python:3.12-slim\n",
            exposed_ports=[1337],
            startup_command="python app.py",
        ),
    )


def test_solver_prompt_includes_solver_failure_diagnostics():
    state = _state()
    state.solver = ChallengeSolver(
        solve_script="print('old-solver')\n",
        solve_language="python",
        dependencies=["requests"],
        expected_flag="CTF{abcdefgh}",
        solve_steps=["a", "b"],
    )
    state.validation = ValidationResult(
        passed=False,
        checks=[ValidationCheck(check="solver_run", passed=False, detail="exit 1")],
        errors=["solver failed"],
    )

    prompt = _build_solver_prompt(state, rag_context="RAG")

    assert "YOUR PREVIOUS SOLVE SCRIPT FAILED" in prompt
    assert "Likely solver-script issues" in prompt
    assert "solver_run: exit 1" in prompt


def test_solver_prompt_includes_failed_solver_history_script():
    state = _state()
    state.solver = ChallengeSolver(
        solve_script="print('current')\n",
        solve_language="python",
        dependencies=["requests"],
        expected_flag="CTF{abcdefgh}",
        solve_steps=["a", "b"],
    )
    state.failed_solver_scripts = ["print('older-failed')\n"]
    state.validation = ValidationResult(
        passed=False,
        checks=[],
        errors=["still failing"],
    )

    prompt = _build_solver_prompt(state, rag_context="RAG")

    assert "Most recent failed solver script from retry history" in prompt
    assert "print('older-failed')" in prompt
