"""Indexer unit tests — schema-introspection and rate-limit helpers.

DB-touching paths (`_ensure_schema`, `_index_one`, `main`) are exercised end
to end in the live e2e run; here we cover the small, pure helpers that don't
need a real Postgres or a live Gemini key.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from indexing import indexer


def _mock_conn(fetchone_returns):
    fake_cur = MagicMock()
    fake_cur.fetchone.return_value = fetchone_returns

    @contextmanager
    def fake_cursor():
        yield fake_cur

    fake_conn = MagicMock()
    fake_conn.cursor.side_effect = fake_cursor
    return fake_conn, fake_cur


def test_check_legacy_schema_raises_when_uid_column_present():
    """v1 schema (uid column) must be detected and refused loudly — operator
    needs to drop & reindex, not silently get NOT NULL violations on INSERT."""
    conn, _ = _mock_conn(fetchone_returns=("uid",))
    with pytest.raises(RuntimeError, match="Legacy v1 schema"):
        indexer._check_legacy_schema(conn)


def test_check_legacy_schema_passes_when_uid_absent():
    """Fresh DB or v2 schema (no uid column) → returns cleanly, no exception."""
    conn, _ = _mock_conn(fetchone_returns=None)
    indexer._check_legacy_schema(conn)


def test_check_legacy_schema_queries_information_schema():
    """Detection must go through information_schema (portable) not pg_catalog."""
    conn, cur = _mock_conn(fetchone_returns=None)
    indexer._check_legacy_schema(conn)
    sql_text = cur.execute.call_args[0][0]
    assert "information_schema.columns" in sql_text
    assert "uid" in sql_text
    assert "challenges" in sql_text


def test_check_legacy_schema_error_message_tells_operator_how_to_recover():
    """The error must name the fix — drop + reindex — not just say 'bad schema'."""
    conn, _ = _mock_conn(fetchone_returns=("uid",))
    with pytest.raises(RuntimeError) as exc_info:
        indexer._check_legacy_schema(conn)
    msg = str(exc_info.value)
    assert "DROP TABLE" in msg
    assert "reindex" in msg.lower()


def test_throttle_no_sleep_below_limit(monkeypatch):
    """Below the rate limit, _throttle_embedding_call must not sleep."""
    monkeypatch.setattr(indexer, "_embedding_call_times", deque(maxlen=indexer.EMBEDDING_RATE_LIMIT))
    sleep_calls: list[float] = []
    monkeypatch.setattr(indexer.time, "sleep", lambda s: sleep_calls.append(s))
    indexer._throttle_embedding_call()
    assert sleep_calls == []


def test_throttle_sleeps_when_window_full(monkeypatch):
    """At the rate limit, _throttle_embedding_call must sleep until the oldest call ages out."""
    # Fill the deque with timestamps just now → oldest is ~0s old, so the
    # function should sleep ~EMBEDDING_WINDOW_SECONDS.
    now = 1_000_000.0
    full = deque(
        [now - 0.5] * indexer.EMBEDDING_RATE_LIMIT,
        maxlen=indexer.EMBEDDING_RATE_LIMIT,
    )
    monkeypatch.setattr(indexer, "_embedding_call_times", full)
    monkeypatch.setattr(indexer.time, "monotonic", lambda: now)
    sleep_calls: list[float] = []
    monkeypatch.setattr(indexer.time, "sleep", lambda s: sleep_calls.append(s))
    indexer._throttle_embedding_call()
    assert len(sleep_calls) == 1
    # Window is 60s, oldest is 0.5s old → sleep ≈ 59.5s.
    assert 59.0 < sleep_calls[0] < 60.0


def test_sanitize_jsonb_strips_nul_byte_escape():
    """Postgres JSONB rejects U+0000; json.dumps emits it as the six-char
    escape sequence, which must be stripped before INSERT."""
    item = {"description": "before\x00after"}
    out = indexer._sanitize_jsonb(item)
    # The escape sequence (literal backslash + u0000) must not appear.
    assert "\\u0000" not in out
    assert "before" in out
    assert "after" in out


def test_sanitize_jsonb_preserves_non_ascii():
    """ensure_ascii=False keeps non-ASCII (e.g. em dashes, accents) readable."""
    item = {"description": "café — naïve"}
    out = indexer._sanitize_jsonb(item)
    assert "café" in out
    assert "—" in out
