"""Skill loader: caching, error path, system-prompt composition."""

from __future__ import annotations

import pytest

from agents import skill_loader


@pytest.fixture(autouse=True)
def _clear_skill_cache():
    skill_loader.load_skill.cache_clear()
    yield
    skill_loader.load_skill.cache_clear()


def test_load_skill_returns_file_contents():
    content = skill_loader.load_skill("rules")
    assert "Global Rules" in content


def test_load_skill_missing_raises():
    with pytest.raises(FileNotFoundError):
        skill_loader.load_skill("nonexistent-skill-xyz")


def test_load_skill_is_cached(tmp_path, monkeypatch):
    """Second call should not re-read disk."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "rules.md").write_text("v1", encoding="utf-8")
    monkeypatch.setattr(skill_loader, "SKILLS_DIR", skills_dir)
    skill_loader.load_skill.cache_clear()

    first = skill_loader.load_skill("rules")
    # Mutate the file on disk — cached result should stay v1.
    (skills_dir / "rules.md").write_text("v2", encoding="utf-8")
    second = skill_loader.load_skill("rules")

    assert first == "v1"
    assert second == "v1"


def test_load_system_prompt_combines_rules_and_skill():
    prompt = skill_loader.load_system_prompt("rag_architect")
    assert "Global Rules" in prompt
    assert "Architect Agent" in prompt
    assert "---" in prompt
