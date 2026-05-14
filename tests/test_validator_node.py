"""Validator pure helpers: flag-in-source check and retry-target decision."""

from __future__ import annotations

import pytest

from agents.event_config import EventConfig
from agents.schemas import Category, ChallengeManifest, RetryTarget, ValidationCheck
from graph.nodes.validator_node import (
    _decide_retry_target,
    _flag_in_source_check,
    _flag_matches_regex_check,
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


def _manifest_with_flag(flag: str) -> ChallengeManifest:
    return ChallengeManifest(
        name="test-1",
        category=Category.WEB,
        difficulty=2,
        vulnerability="SQLi",
        language="python",
        services=["web server"],
        tools_required=["requests"],
        flag=flag,
    )


def test_flag_matches_regex_skipped_when_no_event(state):
    state.event = None
    check = _flag_matches_regex_check(state)
    assert check is None


def test_flag_matches_regex_passes_when_match(state):
    state.manifest = _manifest_with_flag("CTF{abcdefgh}")
    state.event = EventConfig(name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    check = _flag_matches_regex_check(state)
    assert check is not None
    assert check.passed
    assert check.check == "flag_matches_regex"


def test_flag_matches_regex_fails_when_no_match(state):
    state.manifest = _manifest_with_flag("WRONG{abcdefgh}")
    state.event = EventConfig(name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    check = _flag_matches_regex_check(state)
    assert check is not None
    assert not check.passed
    assert r"^CTF\{[a-z]{8,}\}$" in check.detail


def test_flag_matches_regex_rejects_partial_match(state):
    """fullmatch semantics: regex without $ must still match the entire flag."""
    state.manifest = _manifest_with_flag("CTF{abcdefgh}EXTRA")
    state.event = EventConfig(name="t", flag_regex=r"CTF\{[a-z]{8,}\}")
    check = _flag_matches_regex_check(state)
    assert check is not None
    assert not check.passed
