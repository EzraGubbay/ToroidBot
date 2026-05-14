"""Developer prompt composition with event forbidden_techniques."""

from __future__ import annotations

from agents.event_config import EventConfig
from agents.schemas import (
    Category,
    ChallengeManifest,
    ChallengeStory,
    CTFState,
)
from graph.nodes.developer_node import _build_developer_prompt


def _state(event=None, validation=None) -> CTFState:
    return CTFState(
        user_prompt="x",
        manifest=ChallengeManifest(
            name="test-1",
            category=Category.WEB,
            difficulty=2,
            vulnerability="SQLi",
            language="python",
            services=["web server"],
            tools_required=["requests"],
            flag="CTF{abcdefgh}",
        ),
        story=ChallengeStory(
            title="t", description="d", hints=["a", "b"], theme="x",
        ),
        event=event,
        validation=validation,
    )


def test_prompt_no_event_omits_event_block():
    prompt = _build_developer_prompt(_state(), rag_context="RAG")
    assert "EVENT CONSTRAINTS" not in prompt
    assert "RAG" in prompt


def test_prompt_includes_forbidden_techniques():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        forbidden_techniques=["race conditions", "TOCTOU"],
    )
    prompt = _build_developer_prompt(_state(cfg), rag_context="R")
    assert "EVENT CONSTRAINTS" in prompt
    assert "race conditions" in prompt
    assert "TOCTOU" in prompt
