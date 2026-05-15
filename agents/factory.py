"""Factory for creating Pydantic-AI agents from skill files."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agents.skill_loader import load_system_prompt

T = TypeVar("T", bound=BaseModel)

# OpenRouter exposes app_title in its dashboard so teams can attribute spend
# to specific tools. Hardcoded since we have one app, not configurable per-call.
OPENROUTER_APP_TITLE = "ToroidBot"


def _build_model(model: str) -> str | Model:
    """Resolve a model string to either pass-through or an explicit Model.

    For `openrouter:` strings we build an `OpenRouterModel` so we can attach
    `OpenRouterProvider(app_title=...)`. For every other prefix
    (`google-gla:`, `openai:`, `anthropic:`, ...) pydantic-ai infers the
    provider from the string and we let it.
    """
    if model.startswith("openrouter:"):
        # Lazy import — keeps the OpenRouter classes out of the import graph
        # for runs that only use direct providers.
        from pydantic_ai.models.openrouter import OpenRouterModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        _, _, model_name = model.partition(":")
        return OpenRouterModel(
            model_name,
            provider=OpenRouterProvider(app_title=OPENROUTER_APP_TITLE),
        )
    return model


def create_agent(
    skill_name: str,
    output_type: type[T],
    model: str = "google-gla:gemini-2.5-flash",
) -> Agent[None, T]:
    """Create a Pydantic-AI agent from a skill file.

    Args:
        skill_name: Stem of the skill markdown file (e.g. "rag_architect").
        output_type: Pydantic model class for the agent's structured output.
        model: Model string in <provider>:<model> format. Supports
            `google-gla:`, `openai:`, `anthropic:`, `openrouter:` (et al.);
            see pydantic-ai docs for the full list.

    Returns:
        A configured Pydantic-AI Agent instance.
    """
    system_prompt = load_system_prompt(skill_name)

    return Agent(
        _build_model(model),
        output_type=output_type,
        system_prompt=system_prompt,
        # pydantic-ai retries structured-output parse failures internally — bump
        # from the default of 1 to 3 so a single malformed JSON (which happens on
        # very long retry prompts) doesn't kill a multi-minute pipeline run.
        output_retries=3,
    )
