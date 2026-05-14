"""RAG retriever tests with pgvector mocked.

Patches `rag._get_pool` (mock pool whose `.connection()` yields a fake conn
with a programmable cursor) and `rag._embed` (constant zero vector) so tests
don't need a live Postgres or a Gemini API key.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import numpy as np
import psycopg
import pytest
from google.genai import errors as genai_errors

from orchestrator import rag


@pytest.fixture
def fake_db(monkeypatch):
    """Patch _get_pool; return the fake cursor so tests configure .fetchall()."""
    fake_cur = MagicMock()

    @contextmanager
    def fake_cursor():
        yield fake_cur

    fake_conn = MagicMock()
    fake_conn.cursor.side_effect = fake_cursor

    @contextmanager
    def fake_pool_connection():
        yield fake_conn

    fake_pool = MagicMock()
    fake_pool.connection.side_effect = fake_pool_connection
    monkeypatch.setattr(rag, "_get_pool", lambda: fake_pool)
    return fake_cur


@pytest.fixture
def fake_embed(monkeypatch):
    """Patch _embed to return a fixed zero vector — no API calls."""
    fake = MagicMock(return_value=np.zeros(rag.EMBEDDING_DIM))
    monkeypatch.setattr(rag, "_embed", fake)
    return fake


def test_retrieve_returns_empty_message_when_no_matches(fake_db, fake_embed):
    fake_db.fetchall.return_value = []
    out = rag.retrieve_similar_challenges("anything")
    assert "No similar challenges" in out


def test_retrieve_formats_single_match(fake_db, fake_embed):
    fake_db.fetchall.return_value = [
        ("abc-uid", "frog-waf", "WAF bypass desc", "web", 3, ["python", "java"]),
    ]
    out = rag.retrieve_similar_challenges("web exploit")
    assert "### frog-waf [web, difficulty 3]" in out
    assert "abc-uid" in out
    assert "WAF bypass desc" in out
    assert "python, java" in out


def test_retrieve_preserves_db_ordering(fake_db, fake_embed):
    fake_db.fetchall.return_value = [
        ("uid-1", "pickle-jail", "pickle desc", "misc", 5, ["python"]),
        ("uid-2", "dynastic", "caesar desc", "crypto", 1, ["python"]),
    ]
    out = rag.retrieve_similar_challenges("query", top_k=2)
    assert out.index("pickle-jail") < out.index("dynastic")


def test_retrieve_passes_top_k_as_limit(fake_db, fake_embed):
    fake_db.fetchall.return_value = []
    rag.retrieve_similar_challenges("q", top_k=7)
    sql, params = fake_db.execute.call_args[0]
    assert "LIMIT %s" in sql
    assert params[1] == 7


def test_retrieve_sql_uses_pgvector_distance_operator(fake_db, fake_embed):
    fake_db.fetchall.return_value = []
    rag.retrieve_similar_challenges("q")
    sql, _ = fake_db.execute.call_args[0]
    assert "embedding <=> %s" in sql
    assert "ORDER BY" in sql


def test_retrieve_sql_selects_languages(fake_db, fake_embed):
    fake_db.fetchall.return_value = []
    rag.retrieve_similar_challenges("q")
    sql, _ = fake_db.execute.call_args[0]
    assert "languages" in sql


def test_retrieve_handles_db_failure_gracefully(fake_embed, monkeypatch):
    def boom():
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(rag, "_get_pool", boom)
    out = rag.retrieve_similar_challenges("anything")
    assert "RAG retrieval unavailable" in out
    # Error details must NOT leak into the LLM-bound output — they go to logs only.
    assert "connection refused" not in out


def test_retrieve_handles_embed_failure_gracefully(fake_db, monkeypatch):
    def boom(text):
        raise genai_errors.APIError(500, {"error": {"message": "internal"}}, MagicMock())
    monkeypatch.setattr(rag, "_embed", boom)
    out = rag.retrieve_similar_challenges("anything")
    assert "RAG retrieval unavailable" in out


def test_retrieve_handles_pool_timeout(fake_embed, monkeypatch):
    """PoolTimeout is not a psycopg.Error subclass — must be caught explicitly."""
    import psycopg_pool

    def boom():
        raise psycopg_pool.PoolTimeout("pool exhausted")
    monkeypatch.setattr(rag, "_get_pool", boom)
    out = rag.retrieve_similar_challenges("anything")
    assert "RAG retrieval unavailable" in out


def test_retrieve_handles_embedding_error(fake_db, monkeypatch):
    """EmbeddingError (empty / malformed provider response) must not crash."""
    def boom(text):
        raise rag.EmbeddingError("no embeddings returned")
    monkeypatch.setattr(rag, "_embed", boom)
    out = rag.retrieve_similar_challenges("anything")
    assert "RAG retrieval unavailable" in out


def test_retrieve_does_not_swallow_programming_errors(fake_db, monkeypatch):
    """Narrow except must let bugs like AttributeError propagate."""
    def boom(text):
        raise AttributeError("typo in field name")
    monkeypatch.setattr(rag, "_embed", boom)
    with pytest.raises(AttributeError):
        rag.retrieve_similar_challenges("anything")


def test_retrieve_top_k_zero_short_circuits(fake_db, fake_embed):
    """top_k=0 must not query the DB or call the embedder."""
    out = rag.retrieve_similar_challenges("anything", top_k=0)
    assert "No similar challenges" in out
    fake_embed.assert_not_called()
    fake_db.execute.assert_not_called()


def test_retrieve_top_k_negative_short_circuits(fake_db, fake_embed):
    out = rag.retrieve_similar_challenges("anything", top_k=-3)
    assert "No similar challenges" in out
    fake_embed.assert_not_called()


def test_retrieve_passes_query_vector_as_first_param(fake_db, fake_embed):
    """The vector must be the first %s in the SQL (the pgvector distance operand)."""
    fake_db.fetchall.return_value = []
    rag.retrieve_similar_challenges("query")
    _sql, params = fake_db.execute.call_args[0]
    # fake_embed returns np.zeros(EMBEDDING_DIM)
    assert params[0] is not None
    np.testing.assert_array_equal(params[0], np.zeros(rag.EMBEDDING_DIM))


def test_embed_raises_on_empty_embeddings_list(monkeypatch):
    """An empty `embeddings` array (e.g. content-filtered response) must raise EmbeddingError."""
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.embeddings = []
    fake_client.models.embed_content.return_value = fake_response
    monkeypatch.setattr(rag, "_get_client", lambda: fake_client)
    with pytest.raises(rag.EmbeddingError):
        rag._embed("hello")


def test_embed_raises_on_wrong_dim(monkeypatch):
    """Wrong-length vector from the provider must raise EmbeddingError, not silently corrupt."""
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_embedding = MagicMock()
    fake_embedding.values = [0.1, 0.2, 0.3]  # too short
    fake_response.embeddings = [fake_embedding]
    fake_client.models.embed_content.return_value = fake_response
    monkeypatch.setattr(rag, "_get_client", lambda: fake_client)
    with pytest.raises(rag.EmbeddingError):
        rag._embed("hello")


def test_embed_returns_array_on_valid_response(monkeypatch):
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_embedding = MagicMock()
    fake_embedding.values = [0.0] * rag.EMBEDDING_DIM
    fake_response.embeddings = [fake_embedding]
    fake_client.models.embed_content.return_value = fake_response
    monkeypatch.setattr(rag, "_get_client", lambda: fake_client)
    out = rag._embed("hello")
    assert out.shape == (rag.EMBEDDING_DIM,)


def test_format_challenge_brief_layout():
    out = rag._format_challenge_brief("u-1", "name", "desc", "cat", 2, ["python", "bash"])
    assert "### name [cat, difficulty 2]" in out
    assert "**uid:** `u-1`" in out
    assert "**Languages:** python, bash" in out
    assert "**Description:** desc" in out


def test_format_challenge_brief_empty_languages_em_dash():
    out = rag._format_challenge_brief("u-1", "n", "d", "c", 1, [])
    assert "**Languages:** —" in out
