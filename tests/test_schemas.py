"""Schema validation: manifest name regex, defaults, retry_target."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.event_config import EventConfig, PerAgentModels
from agents.schemas import (
    Category,
    ChallengeManifest,
    CTFState,
    RetryTarget,
    ValidationResult,
)


def _base_kwargs() -> dict:
    return dict(
        category=Category.WEB,
        difficulty=2,
        vulnerability="SQL injection",
        language="python",
        services=["web server"],
        tools_required=["requests"],
        flag="CTF{x}",
    )


@pytest.mark.parametrize("name", [
    "sample-web-1",
    "abc",
    "a-b-c-d",
    "0challenge9",
])
def test_manifest_name_accepts_safe_identifiers(name):
    m = ChallengeManifest(name=name, **_base_kwargs())
    assert m.name == name


@pytest.mark.parametrize("name", [
    "Sample-Web",        # uppercase
    "sample_web",         # underscore
    "sample web",         # space
    "-sample",            # leading hyphen
    "sample-",            # trailing hyphen
    "a",                  # too short (pattern requires 2+ chars)
    "$(rm -rf /)",        # shell metachars
    "../etc/passwd",      # path traversal
    "",                   # empty
])
def test_manifest_name_rejects_unsafe_identifiers(name):
    with pytest.raises(ValidationError):
        ChallengeManifest(name=name, **_base_kwargs())


def test_description_hint_defaults_to_none():
    m = ChallengeManifest(name="x-1", **_base_kwargs())
    assert m.description_hint is None


def test_validation_result_default_retry_target_is_developer():
    v = ValidationResult(passed=False)
    assert v.retry_target == RetryTarget.DEVELOPER


def test_validation_result_accepts_solver_retry_target():
    v = ValidationResult(passed=False, retry_target=RetryTarget.SOLVER)
    assert v.retry_target == RetryTarget.SOLVER


def test_validation_result_accepts_devops_retry_target():
    v = ValidationResult(passed=False, retry_target=RetryTarget.DEVOPS)
    assert v.retry_target == RetryTarget.DEVOPS


def test_ctf_state_tracks_failed_solver_scripts():
    s = CTFState(user_prompt="x")
    assert s.failed_solver_scripts == []


def _state_with_event(**overrides) -> CTFState:
    cfg = EventConfig(
        name="t",
        flag_regex=r"^CTF\{[a-z0-9]{8,}\}$",
        default_model="event:default",
        models=PerAgentModels(architect="event:architect"),
        **overrides,
    )
    return CTFState(user_prompt="x", event=cfg)


def test_state_event_defaults_to_none():
    s = CTFState(user_prompt="x")
    assert s.event is None


def test_model_for_no_event_returns_builtin():
    s = CTFState(user_prompt="x")
    assert s.model_for("architect") == s.model  # "google-gla:gemini-2.5-flash"


def test_model_for_uses_event_default():
    s = _state_with_event()
    assert s.model_for("storyteller") == "event:default"


def test_model_for_per_agent_beats_default():
    s = _state_with_event()
    assert s.model_for("architect") == "event:architect"


def test_model_for_cli_override_wins():
    s = _state_with_event()
    s.set_cli_model_override("cli:override")
    assert s.model_for("architect") == "cli:override"
    assert s.model_for("storyteller") == "cli:override"


def test_model_for_unknown_agent_raises():
    s = _state_with_event()
    with pytest.raises(ValueError):
        s.model_for("unknown_role")
