"""RAG retriever — pgvector similarity over the indexed challenge corpus.

Embeds the user query, retrieves top_k matches by cosine distance, and returns
metadata + id + languages for each. Full file content is NOT injected — this
is parent-document retrieval: agents fetch source / writeups / files on demand
via a separate id-keyed tool (planned). Embedding metadata only keeps the
similarity signal clean (NL queries vs. mixed-language code) and the per-row
vectors small enough for cheap HNSW retrieval.
"""

from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

import numpy as np
import psycopg
import psycopg_pool
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from pgvector.psycopg import register_vector
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from orchestrator.rag_config import EMBEDDING_DIM

# Resolve .env relative to this file so the loader doesn't depend on CWD.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

logger = logging.getLogger(__name__)

# Lazy-init: importing this module must not open a DB connection or require
# GEMINI_API_KEY. Callers / tests that don't exercise RAG should pay nothing.
_client: genai.Client | None = None
_pool: ConnectionPool | None = None


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider returns an unusable response."""


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _client


def _get_pool() -> ConnectionPool:
    """Lazy, thread-safe connection pool with vector adapter pre-registered."""
    global _pool
    if _pool is None:
        try:
            password = os.environ["DB_PASSWORD"]
        except KeyError as e:
            raise RuntimeError(
                "DB_PASSWORD is required for RAG retrieval. "
                "Set it in .env or the environment."
            ) from e
        conninfo = make_conninfo(
            dbname=os.getenv("DB_NAME", "vectordb"),
            user=os.getenv("DB_USER", "admin"),
            password=password,
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
        )
        new_pool = ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=10,
            configure=register_vector,
            kwargs={"connect_timeout": 10},
            timeout=15.0,
            open=False,
        )
        try:
            new_pool.open(wait=True, timeout=10)
        except psycopg_pool.PoolTimeout:
            # Leave _pool as None so the next call can retry cleanly instead of
            # operating on a half-opened pool.
            logger.exception("DB pool failed to open within 10s")
            raise
        _pool = new_pool
    return _pool


@atexit.register
def _close_pool() -> None:
    """Close the pool before interpreter shutdown to avoid worker-thread
    join errors from psycopg_pool's destructor."""
    global _pool
    if _pool is not None and not _pool.closed:
        _pool.close()


def _embed(text: str) -> np.ndarray:
    """Embed text via Gemini. Raises EmbeddingError on empty / malformed responses."""
    result = _get_client().models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config={"output_dimensionality": EMBEDDING_DIM},
    )
    if not result.embeddings:
        raise EmbeddingError("Embedding provider returned no embeddings")
    values = result.embeddings[0].values
    if values is None or len(values) != EMBEDDING_DIM:
        raise EmbeddingError(
            f"Embedding provider returned wrong shape: "
            f"got len={len(values) if values is not None else 'None'}, expected {EMBEDDING_DIM}"
        )
    return np.array(values)


def _format_challenge_brief(
    challenge_id: str,
    name: str,
    description: str,
    category: str,
    difficulty: int,
    languages: list[str],
) -> str:
    """Render a retrieved challenge as a metadata-only block.

    The Architect will later use the id to fetch full file content on demand.
    """
    langs_str = ", ".join(languages) if languages else "—"
    return (
        f"### {name} [{category}, difficulty {difficulty}]\n"
        f"**id:** `{challenge_id}`\n"
        f"**Languages:** {langs_str}\n"
        f"**Description:** {description}"
    )


def retrieve_similar_challenges(query: str, top_k: int = 3) -> str:
    """Find challenges relevant to the query via pgvector similarity.

    Returns metadata (name, description, category, difficulty, languages) plus
    id for each match. Agents that need file contents can fetch them via a
    future tool keyed on the returned id.

    Args:
        query: The user's prompt or search terms.
        top_k: Number of results to return.

    Returns:
        Formatted string for inclusion in agent prompts.
    """
    if top_k <= 0:
        return "No similar challenges found in the knowledge base."
    try:
        query_vector = _embed(query)
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, description, category, difficulty, languages
                    FROM challenges
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (query_vector, top_k),
                )
                rows = cur.fetchall()
    except (psycopg.Error, psycopg_pool.PoolTimeout, genai_errors.APIError, EmbeddingError):
        # Detailed exception (which may include DSN bits or stack-trace-flavored
        # text) only goes to logs — agents see a generic, prompt-safe message.
        logger.exception("RAG retrieval failed")
        return "RAG retrieval unavailable."

    if not rows:
        return "No similar challenges found in the knowledge base."

    parts = ["## Similar challenges from the knowledge base:\n"]
    parts.extend(_format_challenge_brief(*row) for row in rows)
    return "\n\n".join(parts)
