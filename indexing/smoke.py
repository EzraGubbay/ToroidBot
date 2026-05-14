"""Smoke test for the indexed RAG corpus.

Runs a handful of natural-language queries through the retriever and prints
the top matches. Use after `python -m indexing.indexer` to confirm the
pgvector path is healthy end-to-end.
"""

from __future__ import annotations

from orchestrator.rag import retrieve_similar_challenges

QUERIES = [
    "pickle deserialization exploit",
    "caesar cipher decryption",
    "web waf bypass via reflection",
]


def main() -> None:
    for query in QUERIES:
        print(f"\n=== Query: {query} ===")
        print(retrieve_similar_challenges(query, top_k=3))


if __name__ == "__main__":
    main()
