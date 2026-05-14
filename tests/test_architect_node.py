"""Architect prompt composition with event constraints."""

from __future__ import annotations

import pytest

from agents.event_config import Audience, EventConfig
from agents.schemas import Category, CTFState
from graph.nodes.architect_node import _build_architect_prompt


def _state(event=None) -> CTFState:
    return CTFState(user_prompt="medium web SQLi", event=event)


def test_prompt_no_event_omits_event_block():
    prompt = _build_architect_prompt(_state(), rag_context="RAG_BODY")
    assert "EVENT CONSTRAINTS" not in prompt
    assert "medium web SQLi" in prompt
    assert "RAG_BODY" in prompt


def test_prompt_includes_flag_regex_when_event_set():
    cfg = EventConfig(name="t", flag_regex=r"^MEGA\{[a-z]{8,}\}$")
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "EVENT CONSTRAINTS" in prompt
    assert r"^MEGA\{[a-z]{8,}\}$" in prompt


def test_prompt_includes_audience():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        audience=Audience.BEGINNER,
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Audience: beginner" in prompt


def test_prompt_includes_forbidden_categories():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        forbidden_categories=[Category.PWN, Category.REV],
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Forbidden categories" in prompt
    assert "pwn" in prompt and "rev" in prompt


def test_prompt_includes_forbidden_techniques():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        forbidden_techniques=["race conditions", "time-based oracles"],
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Forbidden techniques" in prompt
    assert "race conditions" in prompt


def test_prompt_includes_theme_when_set():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        theme="corporate espionage",
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Theme: corporate espionage" in prompt


def test_prompt_omits_theme_when_unset():
    cfg = EventConfig(name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Theme:" not in prompt
