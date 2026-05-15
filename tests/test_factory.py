"""Tests for the agent factory's model construction path.

Verifies that `openrouter:` strings get an explicit `OpenRouterModel` with
`app_title="ToroidBot"` attached, while every other prefix is passed through
to pydantic-ai's string-based provider inference.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic_ai.models.openrouter import OpenRouterModel

from agents import factory


class _OutputStub(BaseModel):
    text: str


@pytest.fixture
def fake_provider_keys(monkeypatch):
    """Set every provider key pydantic-ai's string inference might check.

    Agents validate provider env vars at *construction* time, not first
    request, so tests that build an Agent for any provider need a non-empty
    placeholder key.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_build_model_passes_through_non_openrouter_strings():
    """google-gla / openai / anthropic / etc. are handed to pydantic-ai as-is."""
    for s in (
        "google-gla:gemini-2.5-flash",
        "openai:gpt-4.1",
        "anthropic:claude-sonnet-4-5",
    ):
        assert factory._build_model(s) == s


def test_build_model_constructs_openrouter_model_with_app_title(fake_provider_keys):
    """`openrouter:` triggers an OpenRouterModel with the team's app_title attached.

    `app_title` becomes the `X-Title` HTTP header on the underlying AsyncOpenAI
    client — that's what OpenRouter's dashboard reads for spend attribution.

    NOTE: this assertion reaches into pydantic-ai's private attributes
    (`_provider`, `_client`) because that's where the header lives. A
    pydantic-ai version bump may rename or reshape those internals; if this
    assertion breaks after an upgrade, verify the header path on the new
    version rather than removing the test — the X-Title contract with
    OpenRouter is the thing we actually care about pinning.
    """
    model = factory._build_model("openrouter:anthropic/claude-sonnet-4-5")
    assert isinstance(model, OpenRouterModel)
    assert model._provider._client.default_headers["X-Title"] == factory.OPENROUTER_APP_TITLE


def test_build_model_preserves_slash_in_openrouter_model_name(fake_provider_keys):
    """OpenRouter model names contain a slash (provider/model) — `partition` keeps it intact."""
    model = factory._build_model("openrouter:google/gemini-2.5-flash")
    assert model.model_name == "google/gemini-2.5-flash"


def test_create_agent_uses_build_model_helper_for_openrouter(fake_provider_keys):
    """End-to-end: create_agent threads the model string through _build_model."""
    agent = factory.create_agent(
        skill_name="rules",
        output_type=_OutputStub,
        model="openrouter:google/gemini-2.5-flash",
    )
    assert isinstance(agent.model, OpenRouterModel)


def test_create_agent_pass_through_for_direct_provider(fake_provider_keys):
    """Non-openrouter strings keep producing string-inferred providers."""
    agent = factory.create_agent(
        skill_name="rules",
        output_type=_OutputStub,
        model="google-gla:gemini-2.5-flash",
    )
    # Built with pydantic-ai's GoogleModel inference, not OpenRouterModel.
    assert not isinstance(agent.model, OpenRouterModel)
