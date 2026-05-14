"""Validator pure helpers: flag-in-source check and retry-target decision."""

from __future__ import annotations

import pytest

from agents.schemas import RetryTarget, ValidationCheck
from graph.nodes.validator_node import (
    _decide_retry_target,
    _flag_in_source_check,
    _require_pipeline_outputs,
)


def test_flag_in_source_passes_when_flag_absent(state):
    check = _flag_in_source_check(state)
    assert check.passed
    assert check.check == "flag_not_in_source"


def test_flag_in_source_fails_when_flag_present(state, code_with_flag_leak):
    state.code = code_with_flag_leak
    check = _flag_in_source_check(state)
    assert not check.passed
    assert "app.py" in check.detail


def test_decide_retry_target_blames_solver_when_only_solver_checks_fail():
    checks = [
        ValidationCheck(check="flag_not_in_source", passed=True),
        ValidationCheck(check="docker_build", passed=True),
        ValidationCheck(check="container_start", passed=True),
        ValidationCheck(check="solver_run", passed=False, detail="exit 1"),
        ValidationCheck(check="flag_captured", passed=False),
    ]
    assert _decide_retry_target(checks) == RetryTarget.SOLVER


def test_decide_retry_target_blames_developer_when_build_fails():
    checks = [
        ValidationCheck(check="docker_build", passed=False, detail="syntax error"),
        ValidationCheck(check="solver_run", passed=False),
    ]
    assert _decide_retry_target(checks) == RetryTarget.DEVELOPER


def test_decide_retry_target_defaults_to_developer_when_all_pass():
    checks = [ValidationCheck(check="x", passed=True)]
    assert _decide_retry_target(checks) == RetryTarget.DEVELOPER


def test_require_pipeline_outputs_raises_when_incomplete(state):
    state.solver = None
    with pytest.raises(RuntimeError) as exc:
        _require_pipeline_outputs(state)
    assert "solver" in str(exc.value)


def test_require_pipeline_outputs_silent_when_complete(state):
    _require_pipeline_outputs(state)  # no raise
