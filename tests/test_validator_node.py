"""Validator pure helpers: flag-in-source check and retry-target decision."""

from __future__ import annotations

import pytest

from agents.event_config import EventConfig
from agents.schemas import Category, ChallengeManifest, ChallengeCode, RetryTarget, ValidationCheck
from graph.nodes.validator_node import (
    _decide_retry_target,
    _flag_in_source_check,
    _flag_matches_regex_check,
    _masked_template_errors_check,
    _secret_key_placeholder_check,
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


def test_decide_retry_target_blames_devops_when_build_fails():
    checks = [
        ValidationCheck(check="docker_build", passed=False, detail="syntax error"),
        ValidationCheck(check="solver_run", passed=False),
    ]
    assert _decide_retry_target(checks) == RetryTarget.DEVOPS


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


def test_secret_key_placeholder_check_flags_placeholder_secret(state):
    state.code = ChallengeCode(
        files={"app.py": "app.config['SECRET_KEY'] = 'your-secret-key-here'\n"},
        entry_point="app.py",
        flag_location="/flag.txt",
        intended_vulnerability="x",
    )
    check = _secret_key_placeholder_check(state)
    assert check is not None
    assert not check.passed
    assert "SECRET_KEY" in check.detail


def test_masked_template_errors_check_flags_generic_exception_wrapper(state):
    state.code = ChallengeCode(
        files={
            "app.py": (
                "from flask import render_template_string\n"
                "try:\n"
                "    rendered = render_template_string(bio)\n"
                "except Exception:\n"
                "    return jsonify({'status': 'error', 'message': 'An error occurred while processing your request'})\n"
            )
        },
        entry_point="app.py",
        flag_location="/flag.txt",
        intended_vulnerability="x",
    )
    check = _masked_template_errors_check(state)
    assert check is not None
    assert not check.passed
    assert "masks template/rendering errors" in check.detail
