"""Shared RAG constants — kept in their own module so importing them from
indexer doesn't pull in the retriever's runtime dependencies."""

from __future__ import annotations

# Matryoshka reduction so the column fits HNSW's 2000-dim cap on pgvector.
# Both the indexer (when calling embed_content) and the retriever (for the
# query vector + column type) must agree on this dimension.
EMBEDDING_DIM = 768
