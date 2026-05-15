# End-to-End Testing Guide

How to verify ToroidBot works at every layer — from a single agent's prompt-composition logic up to a full pipeline run that produces a deployable CTF challenge artifact.

This guide assumes you've read [`docs/agents_description.md`](./agents_description.md) for an overview of the six agents (Architect → Storyteller → Developer → DevOps → Solver → Validator) and the [README](../README.md) for the project's input modes.

## Test surface — what each layer covers

| Layer | Scope | LLM? | DB? | Docker? | Cost | Speed |
|---|---|---|---|---|---|---|
| **1. Unit** (mocked) | One module / one node's deterministic logic | mocked | mocked | mocked | $0 | seconds |
| **2. RAG** | `orchestrator/rag.py` + `indexing/indexer.py` against real pgvector | embeddings only | real | real | $0 (Gemini free tier + throttle) | ~1 min reindex / <1s per retrieval |
| **3. Per-agent live** | One agent against a real LLM with a hand-seeded state | real | real (if Architect/Developer/Solver) | no | a few cents | seconds–minutes per agent |
| **4. Full pipeline** | All six agents end-to-end with validator + Docker sandbox | real | real | real | tens of cents | 1–5 min per run |
| **5. REST API** | FastAPI endpoints over the pipeline | mocked-or-real | mocked-or-real | mocked-or-real | varies | fast (mocked) or full-pipeline (live) |

## Prerequisites

Local environment:

```bash
uv sync --extra dev                                          # Python deps
docker compose -f infrastructure/docker-compose.yml up -d    # pgvector + pgadmin
```

Required environment variables (in `.env`, gitignored):

- **`DB_PASSWORD`** — always required if you touch RAG retrieval or indexing.
- **`GEMINI_API_KEY`** — required for **RAG embeddings** (used by indexer and retriever; stays on AI Studio regardless of which provider drives chat).
- **`OPENROUTER_API_KEY`** — required if any agent's model string is `openrouter:...`.
- **`OPENAI_API_KEY`**, **`ANTHROPIC_API_KEY`** — only if you route specific agents to `openai:...` or `anthropic:...`.

If you're running the full pipeline against `examples/configs/gemini-only.yaml`, only `GEMINI_API_KEY` + the DB vars are needed.

## Layer 1 — Unit tests (always run these first)

Fully mocked; no network, no DB, no Docker. Fast feedback loop.

```bash
uv run pytest --ignore=tests/test_sandbox_e2e.py
```

Should be **green on a clean checkout**. The Docker sandbox integration test is excluded because it requires a Docker daemon. Run it on its own:

```bash
uv run pytest tests/test_sandbox_e2e.py
```

### What's covered per file

| Test file | What it pins |
|---|---|
| `test_architect_node.py` | Architect prompt composition (event constraints inject correctly, no event = no event block) |
| `test_storyteller_node.py` | Storyteller prompt composition + event style injection (tone, theme, organizer, language) |
| `test_developer_node.py` | Developer prompt composition + `forbidden_techniques` injection |
| `test_validator_node.py` | Validator's deterministic checks (flag-in-source, flag-regex match) + LLM-review error paths |
| `test_event_config.py` | `EventConfig` YAML/JSON loading, regex enforcement, slugification |
| `test_factory.py` | `create_agent` model resolution; `openrouter:` strings get `OpenRouterModel` with `X-Title` header |
| `test_main.py` | CLI argument parsing, `--config` / `--model` / `--max-retries` / `--no-sandbox` overrides |
| `test_rag.py` | RAG retriever with mocked pgvector pool — formatting, top_k, error sanitization, embedding validation |
| `test_output.py` | Challenge artifact writing (filesystem layout under `output/<event-slug>/<challenge>/`) |
| `test_schemas.py` | Pydantic model validation for all pipeline I/O types |
| `test_skill_loader.py` | `skills/*.md` system-prompt loading |
| `test_docker_runtime.py` | `DockerSandbox` mocking and protocol contract |
| `test_api_endpoints.py` | FastAPI routes — challenge generation, knowledge base, settings, presets |

## Layer 2 — RAG against real pgvector

The unit tests for `test_rag.py` are fully mocked. To validate the **real** pgvector path:

```bash
# 1. Bring up the stack
docker compose -f infrastructure/docker-compose.yml up -d

# 2. Index the dataset (uses GEMINI_API_KEY for embeddings)
uv run python -m indexing.indexer

# 3. Smoke-check retrieval
uv run python -m indexing.smoke
```

Expected:

- Indexer indexes ~138 challenge items (some files contain arrays, hence > the 100 JSON file count).
- `0 failures, 0 warnings` is the success bar.
- Smoke prints 3 queries × 3 neighbors each; results should be **semantically on-target** (caesar query → "Dynastic"; pickle query → "Pickle Phreaks Revenge"; WAF query → "frog-waf").

If indexer fails:

- **`429 RESOURCE_EXHAUSTED`** — Gemini free-tier limit (100 embedding calls/min). The indexer has a built-in throttle + 429 retry; if it still fails, your key shared quota with another process. Wait or use a paid key.
- **`vector type not found in the database`** — pgvector extension missing. Should be impossible if `_ensure_schema` ran; check Postgres logs.
- **`UntranslatableCharacter: unsupported Unicode escape sequence`** — dataset has `U+0000` somewhere; the indexer strips these via `_sanitize_jsonb` at INSERT time. If it surfaces, the JSON content has a deeply-nested null byte that escaped the strip.

## Layer 3 — Per-agent live testing

Each agent in `graph/nodes/*.py` exposes an async `run(state: CTFState) -> CTFState` function. You can invoke them individually as long as you seed `state` with the outputs of all upstream agents.

### Pattern: hand-seed a state and call one node

```python
# scratch/test_storyteller_live.py
import asyncio
from agents.schemas import CTFState, ChallengeManifest, Category
from graph.nodes import storyteller_node

async def main():
    state = CTFState(user_prompt="A noir-themed crypto challenge")
    # Hand-seed what the Architect would have produced:
    state.manifest = ChallengeManifest(
        name="Vault of Whispers",
        category=Category.CRYPTO,
        difficulty=3,
        vulnerability="AES nonce reuse",
        flag="CTF{n0nce_r3us3_is_d34dly}",
        # ... other required fields ...
    )
    state = await storyteller_node.run(state)
    print(state.story.model_dump_json(indent=2))

asyncio.run(main())
```

Run with `uv run python scratch/test_storyteller_live.py`. Costs one LLM call.

### Per-agent test recipes

| Agent | Standalone? | Required upstream state | What to inspect on output |
|---|---|---|---|
| **Architect** | ✅ Yes | Only `user_prompt` (and optional `event`). Hits RAG. | `state.manifest` — name, category, difficulty, vulnerability, flag conform to event constraints |
| **Storyteller** | ✅ Yes | `manifest` | `state.story` — title, description in event tone/theme |
| **Developer** | ✅ Yes | `manifest`, `story`. Hits RAG. | `state.code.files` — one exploitable flaw matching the manifest |
| **DevOps** | ✅ Yes | `manifest`, `code` | `state.infra.dockerfile`, `state.infra.compose` |
| **Solver** | ✅ Yes | `manifest`, `code`, `infra`. Hits RAG. | `state.solver.script` — Python solve script |
| **Validator** | ⚠️ Needs Docker | `manifest`, `code`, `infra`, `solver` | `state.validation.passed`, individual `checks` (flag_not_in_source, flag_matches_regex, sandbox_build, sandbox_solve, llm_review) |

**Validator is the special case** — it runs the full Docker sandbox (build, run, exploit) as deterministic checks, plus a final LLM review. It's the only node that needs a running Docker daemon during live testing. The deterministic checks short-circuit on first failure, so a missing artifact (e.g., no `solver`) will fail fast rather than burn an LLM call.

### Routing a single agent to OpenRouter for a live test

```python
state.event = EventConfig(
    name="solo",
    flag_regex=r"^CTF\{.{8,}\}$",
    models={"architect": "openrouter:anthropic/claude-sonnet-4-5"},
)
state = await architect_node.run(state)
```

`create_agent` resolves the prefix at construction time; per-agent routing works for live tests just like it does for full pipeline runs. Make sure `OPENROUTER_API_KEY` is set.

## Layer 4 — Full pipeline e2e

The canonical "everything works" test:

```bash
docker compose -f infrastructure/docker-compose.yml up -d
uv run python -m orchestrator.main \
    "a beginner web challenge involving cookie tampering" \
    --config examples/configs/gemini-only.yaml
```

Or against OpenRouter (closes PR #12's outstanding gate):

```bash
uv run python -m orchestrator.main \
    "a beginner web challenge involving cookie tampering" \
    --config examples/configs/openrouter.yaml
```

Success looks like:

1. `Event: <event name>` printed (config loaded)
2. Each agent's progress prints to stdout
3. Validator runs all checks; if any fail, retries up to `max_retries`
4. **Artifact written** to `output/<event-slug>/<challenge-slug>/`:
   - `README.md` (player-facing)
   - `Dockerfile` / `docker-compose.yml` (infra)
   - Source files (`app.py`, etc.)
   - `solve.py` (solver script)
   - `manifest.json`, `validation.json` (introspection)

If the run fails:

- **`Challenge generation failed after validation`** — pipeline ran but couldn't produce a valid challenge within `max_retries`. The chosen model is probably too weak. Try a stronger model (`openrouter:anthropic/claude-sonnet-4-5` for Architect/Solver works well).
- **`429 RESOURCE_EXHAUSTED` mid-run** — chat completion quota burned (separate from embedding quota). Gemini free tier is 20/day for `gemini-2.5-flash`. Switch to OpenRouter or wait.
- **`Docker daemon not reachable`** — `--no-sandbox` skips the Validator's Docker checks but leaves the LLM review intact. Use this only when debugging.

### Reading the validation output

The Validator writes a `validation.json` next to the artifact. The structure:

```json
{
  "passed": true,
  "retry_target": null,
  "checks": [
    {"check": "flag_not_in_source", "passed": true, "detail": "..."},
    {"check": "flag_matches_regex", "passed": true, "detail": "..."},
    {"check": "sandbox_build", "passed": true, "detail": "..."},
    {"check": "sandbox_solve", "passed": true, "detail": "..."},
    {"check": "llm_review", "passed": true, "detail": "no extra attack surface found"}
  ]
}
```

If `passed: false`, the `retry_target` field tells the pipeline whether to rerun just the Solver or the whole Build phase next iteration.

## Layer 5 — REST API tests

The FastAPI app (added in PR #8) is tested via `tests/test_api_endpoints.py` with the agents mocked. To run the API live:

```bash
uv run uvicorn api.main:app --reload
# In another shell:
curl -X POST http://localhost:8000/challenges \
    -H "Content-Type: application/json" \
    -d '{"prompt": "easy web SQLi"}'
```

The endpoint mounts the same pipeline you run via CLI — the only added surface is HTTP handling, auth, and SSE/WebSocket streaming. The mocked unit tests cover routing + auth + serialization; full-pipeline behavior is the same as Layer 4.

## Cost & throttling notes

- **Embedding quota** (Gemini free tier): 100 requests/min, 1500/day. The indexer's `_throttle_embedding_call` keeps you under the per-minute. A full reindex of the current dataset (~138 items) takes ~80s due to the throttle.
- **Generation quota** (Gemini free tier): 20/day on `gemini-2.5-flash`, 10/day on `gemini-2.5-flash-lite`, 0/day on `gemini-2.5-pro`. A single full pipeline run makes 6+ generation calls — you'll exhaust free tier in ~3 runs.
- **OpenRouter** is the recommended live-testing provider once dataset/key allowances scale beyond Gemini's free tier. `app_title="ToroidBot"` is set automatically for spend attribution.

## Troubleshooting flowchart

```
Unit tests fail?
  └─> Layer 1 problem. Run `uv run pytest -xvs` to see the first failure verbatim.

Unit tests pass but indexer fails?
  └─> Layer 2 problem.
       ├─ vector type not found  →  pgvector extension; check Postgres logs / re-run indexer
       ├─ 429                    →  rate limit; wait or use a paid key
       └─ UntranslatableChar     →  dataset corruption; check the failing file

Indexer succeeds but smoke retrieval is bad?
  └─> Embedding model mismatch (indexer's EMBEDDING_DIM != retriever's), or
      query / corpus distributions are too far apart. Check rag_config.EMBEDDING_DIM
      is set identically and the indexer ran against the same model as the retriever.

Per-agent live test fails to construct prompt?
  └─> Missing upstream state. Re-read the "Required upstream state" column.

Full pipeline fails after validation?
  └─> Layer 4 problem. Read `validation.json`:
       ├─ flag_not_in_source / flag_matches_regex fail  →  Developer issue; stronger Developer model
       ├─ sandbox_build / sandbox_solve fail            →  Developer or DevOps issue; stronger model or simpler manifest
       └─ llm_review fails                              →  qualitative finding; check `detail` field

API tests fail but CLI works?
  └─> Layer 5 problem. Auth, serialization, or SSE/WebSocket handling. CLI bypasses these.
```

## What's not yet covered

- **Automated CI** — there's no GitHub Actions workflow yet. Tracked in [#6](https://github.com/Tzadikimctf/ToroidBot/issues/6). Until then, all of Layers 1–5 must be run locally before merging.
- **Per-agent live tests as pytest fixtures** — currently you write a one-off script as shown above. A `--live-llm` pytest flag with per-agent parametrization would be a useful follow-up, but cost containment matters: each test run hits paid APIs.
- **Multi-config matrix** — running the same prompt through `gemini-only.yaml`, `openrouter.yaml`, and `megactf-2026.yaml` to verify per-agent routing flows correctly. Currently a manual exercise.
