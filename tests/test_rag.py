"""RAG retriever: corpus loading, scoring, formatting, fence-safety, caching."""

from __future__ import annotations

import json

import pytest

from orchestrator import rag


@pytest.fixture(autouse=True)
def _isolated_rag_dir(tmp_path, monkeypatch):
    rag._load_challenges.cache_clear()
    data_dir = tmp_path / "rag_data"
    data_dir.mkdir()
    monkeypatch.setattr(rag, "RAG_DATA_DIR", data_dir)
    yield data_dir
    rag._load_challenges.cache_clear()


def _write_challenge(data_dir, payload):
    path = data_dir / f"{payload['task_name']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_challenges_returns_empty_when_dir_missing(monkeypatch, tmp_path):
    rag._load_challenges.cache_clear()
    monkeypatch.setattr(rag, "RAG_DATA_DIR", tmp_path / "does-not-exist")
    assert rag._load_challenges() == ()


def test_load_challenges_caches(_isolated_rag_dir):
    _write_challenge(_isolated_rag_dir, {"task_name": "ch1", "category": "web"})
    first = rag._load_challenges()
    # Add a new file after the first read; cached result should not include it.
    _write_challenge(_isolated_rag_dir, {"task_name": "ch2", "category": "web"})
    second = rag._load_challenges()
    assert first == second
    assert len(first) == 1


def test_score_challenge_matches_keywords():
    ch = {
        "task_name": "sqli-login",
        "description": "exploit SQL injection in login",
        "category": "web",
        "difficulty": 2,
        "files": [],
    }
    assert rag._score_challenge(ch, {"sql", "injection"}) == 2
    assert rag._score_challenge(ch, {"buffer", "overflow"}) == 0


def test_retrieve_similar_challenges_picks_highest_score(_isolated_rag_dir):
    _write_challenge(_isolated_rag_dir, {
        "task_name": "sqli-login",
        "description": "SQL injection in login form",
        "category": "web",
        "difficulty": 2,
        "files": [{"path": "app.py", "language": "python", "content": "SELECT *"}],
    })
    _write_challenge(_isolated_rag_dir, {
        "task_name": "buffer-overflow",
        "description": "binary exploitation",
        "category": "pwn",
        "difficulty": 4,
        "files": [],
    })
    result = rag.retrieve_similar_challenges("web sql injection login")
    assert "sqli-login" in result
    # buffer-overflow may or may not appear depending on terms — but if both present,
    # sqli-login must be the top match.
    sqli_idx = result.index("sqli-login")
    if "buffer-overflow" in result:
        assert sqli_idx < result.index("buffer-overflow")


def test_retrieve_handles_empty_dataset(_isolated_rag_dir):
    out = rag.retrieve_similar_challenges("anything")
    assert "No similar challenges" in out


def test_fenced_neutralizes_inner_backticks():
    """Inner ``` would otherwise close the outer fence and produce broken markdown."""
    out = rag._fenced("code with ``` inside", lang="python", max_chars=1000)
    # The triple-backtick should not appear inside the fenced block.
    inner = out.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert "```" not in inner
    # Fence is still well-formed at the start/end.
    assert out.startswith("```python\n")
    assert out.endswith("\n```")


def test_fenced_truncates_to_max_chars():
    out = rag._fenced("x" * 5000, lang="", max_chars=100)
    inner = out.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert len(inner) == 100
