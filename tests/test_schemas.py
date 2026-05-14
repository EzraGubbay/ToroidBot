"""Schema validation: manifest name regex, defaults, retry_target."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.schemas import (
    Category,
    ChallengeManifest,
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
