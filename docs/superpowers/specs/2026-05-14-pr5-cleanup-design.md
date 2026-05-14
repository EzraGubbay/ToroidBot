# PR #5 Cleanup — Design Spec

**Date:** 2026-05-14
**Status:** Approved for implementation
**Author:** noahkmoore + Claude
**Target PR:** [#5 RAG knowledge base: pgvector indexer + retrieval over dataset](https://github.com/Tzadikimctf/ToroidBot/pull/5)

## Summary

Clean up the draft RAG-knowledge-base PR (`rag-knowledge-base-rebased`) to make it review-ready: remove an unused dependency, add a CLI script entry for the indexer, fix the PR description, and plan the second rebase that will be needed after PR #3 lands. CI is deferred to a follow-up issue. The downstream code-review pass and the OpenRouter integration are separate workstreams handled outside this spec.

## Goals

- Land the pgvector-backed RAG implementation on `main` without dragging in unused deps or inaccurate docs.
- Make the indexer ergonomic to run (`ctf-index` instead of `python -m indexing.indexer`).
- Document a clear path through the post-PR-#3 rebase so the reviewer isn't surprised by conflicts.
- Preserve @EzraGubbay's authorship on the two RAG commits already cherry-picked.

## Non-goals

- Adding CI in this PR (tracked as a follow-up issue).
- Adding integration tests that hit a real pgvector (unit tests are mocked; smoke is manual).
- Touching anything outside the RAG/indexing surface — no orchestrator agents, no validator, no event-config changes.
- Designing the OpenRouter integration (separate brainstorm cycle after this PR merges).

## Changes

### 1. `pyproject.toml`

- **Remove** `"sqlalchemy==2.0"` from `[project].dependencies`. It is not imported anywhere in `orchestrator/`, `indexing/`, `agents/`, `graph/`, or `tests/` (only mentioned inside CTF challenge dataset JSON as challenge content).
- **Add** to `[project.scripts]`:
  ```toml
  ctf-index = "indexing.indexer:main"
  ```
  Matches the existing `ctf-poc` entry pattern. `indexing/indexer.py` already defines `def main()` and a `__main__` guard, so no code changes are required to wire it up. No smoke entry — `python -m indexing.smoke` is a one-off manual check.

### 2. `.env.example`

Current state on `rag-knowledge-base-rebased`: `DB_USER`/`DB_PASSWORD`/`DB_NAME`/`PGADMIN_EMAIL`/`PGADMIN_PASSWORD` exist but are **commented out**; `DB_HOST` and `DB_PORT` are **missing entirely**; `EMBEDDING_MODEL` exists but commented. The API-key block is correctly commented.

Edit the DB block to look like this (uncommented, with placeholders that match `docker-compose.yml`'s `${VAR:-default}` fallbacks so a fresh clone Just Works):

```
# Database Configuration
DB_USER=admin
DB_PASSWORD=password123
DB_NAME=vectordb
DB_HOST=localhost
DB_PORT=5432

PGADMIN_EMAIL=admin@toroid.ai
PGADMIN_PASSWORD=admin123
```

Also uncomment `EMBEDDING_MODEL=gemini-embedding-001` so newcomers see the embedding model the indexer expects.

Leave the API-key block commented — that contract is unchanged.

The literal default passwords (`password123`, `admin123`) are intentionally weak local-dev credentials and are out of scope for this PR (they appear in `docker-compose.yml`, `indexer.py`, `rag.py` as fallback values). Tracked as a future hardening task once secrets management is in place.

### 3. `orchestrator/rag.py` — delete legacy keyword-match block

The rewritten retriever leaves the prior keyword-match implementation in place as ~50 lines of commented-out code (with a comment saying "keep around for one PR cycle then delete"). The cycle is this PR.

- Remove the entire "Legacy keyword-match implementation" comment block.
- Remove the two dead commented imports at the top of the file: `# import functools` and `# import json`.
- No functional change — the live code path already replaced this implementation.

### 4. Open the CI follow-up issue (sequencing)

This must happen **before** section 5 (PR description rewrite) so we have the issue number to reference in the PR body.

Open a new issue on `Tzadikimctf/ToroidBot` titled `Add CI workflow (pytest + ruff)` with body covering:
- A `unit` job running `pytest --ignore=tests/test_sandbox_e2e.py` on Ubuntu/Python 3.11.
- A future `integration` job with a `pgvector/pgvector:pg16` service container, gated to a label or schedule.
- A ruff lint step.

Capture the returned issue number/URL for section 5.

### 5. PR #5 description rewrite

Replace the existing draft body. The new body must:

- Drop the `infrastructure/.env` open question (the file isn't in the repo; compose uses `${VAR:-default}` shell fallbacks).
- Replace the sqlalchemy pin question with: "removed — was unused."
- Replace the CI question with a link to the follow-up issue created in section 4.
- Keep the rebase-conflict resolutions note (`requirements.txt`, `.gitignore`, `infrastructure/docker-compose.yml`).
- Keep the test plan (manual smoke + mocked unit tests).

### 6. Post-PR-#3 rebase plan

Verified by inspecting `git diff origin/main origin/feature/config`: the two PRs are **file-disjoint** on the RAG surface. PR #3 modifies `graph/nodes/*`, `orchestrator/main.py`, `orchestrator/output.py`, and adds new test files. It does **not** touch `orchestrator/rag.py`, `tests/test_rag.py`, or `.env.example`.

After PR #3 merges to `main`:

- `git fetch origin && git rebase origin/main` on `rag-knowledge-base-rebased`.
- **Only expected conflict:** `pyproject.toml`. PR #3 adds `"pyyaml>=6.0"` to `[project].dependencies`; PR #5 adds `openai`, `numpy`, `psycopg[binary]`, `psycopg-pool`, `pgvector`, `google-genai` (and removes the unused `sqlalchemy==2.0` per section 1). Resolution: keep both sets, alphabetized.
- Force-push back to `origin/rag-knowledge-base-rebased`.
- **No conflict** in `orchestrator/rag.py`, `tests/test_rag.py`, or `.env.example`.
- PR #3's `graph/nodes/solver_node.py` and `developer_node.py` already call `retrieve_similar_challenges(query, top_k=event.rag_top_k)`. PR #5's rewritten retriever has the same `(query, top_k=3)` signature, so call sites work unmodified. Re-run `pytest --ignore=tests/test_sandbox_e2e.py` post-rebase to confirm.

### 7. Branch hygiene (post-merge)

After PR #5 merges:

- `git push origin --delete rag-knowledge-base` (orphan parallel history)
- `git push origin --delete rag-knowledge-base-rebased`
- Locally: `git branch -D rag-knowledge-base-rebased`

## Validation

Before flipping PR #5 from draft to ready-for-review:

- [ ] `pytest --ignore=tests/test_sandbox_e2e.py` is green on a clean checkout of the rebased branch.
- [ ] `pip install -e .` succeeds and `which ctf-index` resolves to the installed script (or `python -c "from importlib.metadata import entry_points; print([e for e in entry_points(group='console_scripts') if e.name == 'ctf-index'])"` finds the entry). The indexer has no argparse, so we don't run it as part of validation here — that's the manual smoke step below.
- [ ] `docker compose -f infrastructure/docker-compose.yml up -d` brings up pgvector + pgadmin without errors.
- [ ] Manual smoke: `ctf-index` indexes the dataset; `python -m indexing.smoke` returns sensible neighbors for the test queries.
- [ ] PR description on GitHub matches the rewritten body in section 5.
- [ ] PR is mergeable (no conflicts vs current `origin/main`) after the second rebase.
- [ ] `pytest --ignore=tests/test_sandbox_e2e.py` re-run post-rebase is green.

## Code review pass (after cleanup)

Out of scope for the cleanup PR itself, but tracked here so it isn't forgotten. Reviewer focus areas:

- **`orchestrator/rag.py`** — connection pool lifecycle (lazy init, teardown), embedding error handling (`genai_errors`), retry policy, top_k bounds, SQL parameterization.
- **`indexing/indexer.py`** — idempotency on re-run (UNIQUE on `uid`), batching strategy, schema-creation guarded by `IF NOT EXISTS`, embedding dimension consistency with `rag.py`'s `EMBEDDING_DIM`.
- **Dataset JSON edits** — spot check that the `languages` array additions are consistent across all 28 modified files.

## Risks

- **Second rebase introduces a subtle bug** in how `rag_top_k` flows from `event` → node → retriever. Low risk — the retriever signature is identical pre/post-rewrite — but mitigation is to re-run `pytest` and the manual smoke post-rebase.
- **Embedding model availability.** Indexer uses `gemini-embedding-001`. If the team moves to OpenRouter in the next branch, embeddings stay on Gemini AI Studio (OpenRouter does not expose Gemini embeddings). Document this constraint in the OpenRouter design.
- **Orphan branch already deleted on someone's clone.** Deleting `rag-knowledge-base` from origin doesn't delete it from contributor checkouts. Communicate via PR/Slack so no one re-pushes the orphan tip.
