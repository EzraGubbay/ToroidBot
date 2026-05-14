"""Factory for creating Pydantic-AI agents from skill files."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent

from agents.skill_loader import load_system_prompt

T = TypeVar("T", bound=BaseModel)


def create_agent(
    skill_name: str,
    output_type: type[T],
    model: str = "google-gla:gemini-2.5-flash",
) -> Agent[None, T]:
    """Create a Pydantic-AI agent from a skill file.

    Args:
        skill_name: Stem of the skill markdown file (e.g. "rag_architect").
        output_type: Pydantic model class for the agent's structured output.
        model: Model string in <provider>:<model> format.

    Returns:
        A configured Pydantic-AI Agent instance.
    """
    system_prompt = load_system_prompt(skill_name)

    return Agent(
        model,
        output_type=output_type,
        system_prompt=system_prompt,
    )
