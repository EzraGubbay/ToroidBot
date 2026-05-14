"""RAG retriever — searches the knowledge base for similar challenges.

Currently uses keyword matching against challenge metadata.
Future: replace with vector search via pgvector or ChromaDB.
"""

from __future__ import annotations

import json
from pathlib import Path

RAG_DATA_DIR = Path(__file__).resolve().parent.parent / "dataset" / "formated_rag_data"


def _load_challenges() -> list[dict]:
    """Load all challenge JSON files from the RAG data directory."""
    challenges = []
    if not RAG_DATA_DIR.exists():
        return challenges
    for path in sorted(RAG_DATA_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            # Handle both single objects and arrays
            if isinstance(data, list):
                challenges.extend(data)
            else:
                challenges.append(data)
    return challenges


def retrieve_similar_challenges(query: str, top_k: int = 5) -> str:
    """Find challenges relevant to the query using keyword matching.

    Args:
        query: The user's prompt or search terms.
        top_k: Number of results to return.

    Returns:
        Formatted string of matching challenges for inclusion in agent prompts.
    """
    challenges = _load_challenges()
    query_lower = query.lower()
    query_terms = set(query_lower.split())

    scored = []
    for ch in challenges:
        searchable = " ".join([
            ch.get("task_name", ""),
            ch.get("description", ""),
            ch.get("category", ""),
            str(ch.get("difficulty", "")),
        ]).lower()

        score = sum(1 for term in query_terms if term in searchable)
        if score > 0:
            scored.append((score, ch))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for _, ch in scored[:top_k]:
        results.append(
            f"- **{ch.get('task_name', 'Unknown')}** "
            f"[{ch.get('category', '?')}, difficulty {ch.get('difficulty', '?')}]: "
            f"{ch.get('description', 'No description')[:200]}"
        )

    if not results:
        return "No similar challenges found in the knowledge base."

    return "\n".join(results)
