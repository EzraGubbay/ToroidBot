"""Storyteller prompt composition with event tone/theme/organizer."""

from __future__ import annotations

from agents.event_config import EventConfig, Tone
from agents.schemas import Category, ChallengeManifest, CTFState
from graph.nodes.storyteller_node import _build_storyteller_prompt


def _manifest() -> ChallengeManifest:
    return ChallengeManifest(
        name="test-1",
        category=Category.WEB,
        difficulty=2,
        vulnerability="SQLi",
        language="python",
        services=["web server"],
        tools_required=["requests"],
        flag="CTF{abcdefgh}",
    )


def _state(event=None) -> CTFState:
    return CTFState(user_prompt="x", manifest=_manifest(), event=event)


def test_prompt_no_event_omits_event_block():
    prompt = _build_storyteller_prompt(_state())
    assert "EVENT STYLE" not in prompt


def test_prompt_includes_tone():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$", tone=Tone.NOIR,
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Tone: noir" in prompt


def test_prompt_includes_theme_when_set():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        theme="space heist",
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Theme: space heist" in prompt


def test_prompt_includes_organizer_when_set():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        organizer="Aperture Sec Labs",
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Aperture Sec Labs" in prompt


def test_prompt_includes_language_when_non_english():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        language="he",
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Language: he" in prompt


def test_prompt_omits_language_block_when_english():
    """English is the default — don't add noise to the prompt."""
    cfg = EventConfig(name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Language:" not in prompt
