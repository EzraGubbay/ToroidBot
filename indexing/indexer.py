"""Index `dataset/formated_rag_data/*.json` into pgvector.

Parent-document retrieval: only challenge METADATA (name, category, difficulty,
languages, description) is embedded. The full body of each challenge — source,
writeups, files — is stored verbatim in the `content` JSONB column and is
fetched separately by uid via an agent tool (planned). Embedding metadata only
keeps the similarity signal clean and HNSW vectors small.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import psycopg
from dotenv import load_dotenv
from google import genai
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.conninfo import make_conninfo

from orchestrator.rag_config import EMBEDDING_DIM

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATA_DIR = Path(__file__).resolve().parent.parent / "dataset" / "formated_rag_data"
MODEL_ID = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider returns an unusable response."""


def _get_client() -> genai.Client:
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _get_conninfo() -> str:
    try:
        password = os.environ["DB_PASSWORD"]
    except KeyError as e:
        raise RuntimeError(
            "DB_PASSWORD is required for indexing. Set it in .env or the environment."
        ) from e
    return make_conninfo(
        dbname=os.getenv("DB_NAME", "vectordb"),
        user=os.getenv("DB_USER", "admin"),
        password=password,
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        connect_timeout="10",
    )


def _ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # DDL with a parameterized column dimension via psycopg.sql so the
        # value can't drift into an injection vector via env / config.
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS challenges (
                    id          SERIAL PRIMARY KEY,
                    uid         UUID UNIQUE NOT NULL,
                    name        TEXT NOT NULL,
                    description TEXT,
                    category    TEXT,
                    difficulty  INT,
                    languages   TEXT[] NOT NULL DEFAULT '{{}}',
                    content     JSONB,
                    embedding   vector({dim}),
                    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            ).format(dim=sql.Literal(EMBEDDING_DIM))
        )
        # Existing installs from the v1 schema won't have `indexed_at`; add it
        # idempotently so re-running the indexer doesn't fail on the INSERT.
        cur.execute(
            """
            ALTER TABLE challenges
            ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS challenges_embedding_hnsw_idx
            ON challenges USING hnsw (embedding vector_cosine_ops)
            """
        )
        conn.commit()


def _embed(client: genai.Client, text: str) -> np.ndarray:
    """Embed text via Gemini. Raises EmbeddingError on empty / malformed responses."""
    result = client.models.embed_content(
        model=MODEL_ID,
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


def _index_one(client: genai.Client, conn: psycopg.Connection, item: dict) -> None:
    # Parent-document pattern: embed metadata only, store full body verbatim
    # in `content` JSONB. The retrieval signal is the metadata; agents fetch
    # the body on demand via uid. See module docstring for rationale.
    uid = item["uid"]
    name = item.get("task_name", "")
    category = item.get("category", "")
    difficulty = int(item.get("difficulty", 0))
    description = item.get("description", "")
    languages = item.get("languages", []) or []

    embed_text = (
        f"name: {name} | "
        f"category: {category} | "
        f"difficulty: {difficulty} | "
        f"languages: {', '.join(languages)} | "
        f"description: {description}"
    )
    vector = _embed(client, embed_text)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO challenges
                (uid, name, description, category, difficulty, languages, content, embedding, indexed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (uid) DO UPDATE SET
                name        = EXCLUDED.name,
                description = EXCLUDED.description,
                category    = EXCLUDED.category,
                difficulty  = EXCLUDED.difficulty,
                languages   = EXCLUDED.languages,
                content     = EXCLUDED.content,
                embedding   = EXCLUDED.embedding,
                indexed_at  = CURRENT_TIMESTAMP
            """,
            (uid, name, description, category, difficulty, languages, json.dumps(item), vector),
        )
    conn.commit()
    logger.info("Indexed: %s (%s, difficulty %d, languages %s)", name, category, difficulty, languages)


def index_file(client: genai.Client, conn: psycopg.Connection, path: Path) -> None:
    with open(path) as f:
        doc = json.load(f)
    items = doc if isinstance(doc, list) else [doc]
    for item in items:
        _index_one(client, conn, item)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    client = _get_client()
    with psycopg.connect(_get_conninfo()) as conn:
        register_vector(conn)
        _ensure_schema(conn)
        failures: list[tuple[Path, Exception]] = []
        for path in sorted(DATA_DIR.glob("*.json")):
            try:
                index_file(client, conn, path)
            except Exception as e:
                # Keep going so one bad JSON doesn't kill the whole reindex.
                logger.exception("Failed to index %s", path)
                failures.append((path, e))
        if failures:
            logger.error(
                "%d file(s) failed to index: %s",
                len(failures),
                ", ".join(p.name for p, _ in failures),
            )


if __name__ == "__main__":
    main()
