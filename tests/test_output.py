"""Output saver: directory layout, missing-input guard."""

from __future__ import annotations

import json

import pytest

from agents.event_config import EventConfig
from agents.schemas import CTFState
from orchestrator import output


def test_save_challenge_writes_expected_layout(tmp_path, monkeypatch, state):
    monkeypatch.setattr(output, "OUTPUT_DIR", tmp_path)
    out_dir = output.save_challenge(state)

    assert out_dir == tmp_path / "sample-web-1"
    assert (out_dir / "app.py").read_text() == "print('hello')\n"
    assert (out_dir / "Dockerfile").exists()
    assert (out_dir / "solve.py").read_text() == "print('CTF{test-flag-xyz}')\n"
    assert "Sample" in (out_dir / "README.md").read_text()
    meta = json.loads((out_dir / "challenge_meta.json").read_text())
    assert meta["manifest"]["name"] == "sample-web-1"


def test_save_challenge_raises_on_missing_outputs(tmp_path, monkeypatch):
    """Removed asserts must still block incomplete state — raise RuntimeError."""
    monkeypatch.setattr(output, "OUTPUT_DIR", tmp_path)
    incomplete = CTFState(user_prompt="x")
    with pytest.raises(RuntimeError) as exc:
        output.save_challenge(incomplete)
    assert "missing" in str(exc.value).lower()


def test_save_challenge_uses_event_slug_when_event_set(tmp_path, monkeypatch, state):
    monkeypatch.setattr(output, "OUTPUT_DIR", tmp_path)
    state.event = EventConfig(name="MegaCTF 2026", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    out_dir = output.save_challenge(state)
    assert out_dir == tmp_path / "megactf-2026" / "sample-web-1"
    assert (out_dir / "Dockerfile").exists()


def test_save_challenge_no_event_keeps_flat_layout(tmp_path, monkeypatch, state):
    monkeypatch.setattr(output, "OUTPUT_DIR", tmp_path)
    out_dir = output.save_challenge(state)
    assert out_dir == tmp_path / "sample-web-1"
