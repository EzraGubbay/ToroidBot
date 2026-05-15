# API Endpoints Plan — FastAPI surface for ToroidBot

This document lists the exact REST/WebSocket endpoints to add to the FastAPI backend to fully support a frontend that drives the multi-agent CTF generation and verification pipeline. It also lists the Pydantic schemas that should be added to `api/schemas.py` (names + fields) and short usage notes (status codes, streaming vs polling, auth).

Goal: provide a complete, frontend-friendly API to start runs, follow per-stage progress, inspect generated artifacts, manage RAG knowledge, and edit agent `skills/*.md` templates.

---

## High-level API design rules

- All endpoints use JSON except file downloads and streaming endpoints.
- Long-running operations (generation, validation, docker build) return a `202 Accepted` with a `run_id` and are polled or streamed by the frontend.
- Provide both polling endpoints and a WebSocket/SSE streaming endpoint for live updates.
- Admin actions (retry/cancel/reset) require stronger auth (API key / admin role).

---

## Required Pydantic models to add to `api/schemas.py`

Add these models (names and fields are exact; use appropriate types):

- `PromptRequest`
  - `mode: str`  # "intent" | "cve"
  - `difficulty: Optional[str]`  # beginner|easy|medium|hard
  - `category: Optional[str]`  # web|crypto|rev|pwn|misc
  - `topic: Optional[str]`  # free text vulnerability or tool
  - `cve: Optional[str]`  # CVE-YYYY-NNNN
  - `constraints: Optional[dict]`  # language, max_size, require_solver, etc.
  - `model: Optional[str]`  # model/provider string

- `GenerateResponse`
  - `run_id: str`
  - `status: str`  # accepted | started | queued
  - `message: Optional[str]`

- `RunSummary`
  - `run_id: str`
  - `prompt: Optional[PromptRequest]`
  - `status: str`  # pending|running|failed|succeeded|cancelled
  - `started_at: Optional[datetime]`
  - `finished_at: Optional[datetime]`
  - `current_stage: Optional[str]`

- `StageStatus`
  - `name: str`  # Architect, Storyteller, Developer, DevOps, Solver, Validator
  - `status: str`  # queued|running|passed|failed
  - `summary: Optional[str]`
  - `started_at: Optional[datetime]`
  - `finished_at: Optional[datetime]`

- `RunDetail` (response for GET /runs/{run_id})
  - `summary: RunSummary`
  - `stages: List[StageStatus]`
  - `artifacts: List[ArtifactEntry]`
  - `validation: Optional[ValidationResult]`

- `ArtifactEntry`
  - `path: str`  # relative path in output folder
  - `role: str`  # source|docker|solver|readme
  - `agent: str`  # which agent produced it
  - `size: Optional[int]`

- `LogEntry`
  - `timestamp: datetime`
  - `level: str`
  - `message: str`

- `ValidationResult`
  - `passed: bool`
  - `flag: Optional[str]`
  - `logs: Optional[List[LogEntry]]`
  - `error: Optional[str]`

- `KBImportResponse`
  - `id: str`
  - `count: int`

- `SkillFile`
  - `agent: str`
  - `content: str`
  - `sha256: Optional[str]`

- `RootResponse` already exists (keep it)

Note: Import `datetime` from `datetime` and use typing `Optional`, `List`, `Dict`.

---

## Endpoints (exact routes, methods, schemas)

Group A — Core generation & run lifecycle

- POST /generate
  - Description: Start a new generation run for a prompt.
  - Request model: `PromptRequest`
  - Response: `GenerateResponse` (202 Accepted)
  - Notes: Creates a `run_id` and schedules a pipeline worker. The progress can be polled via `/runs/{run_id}` or streamed via WebSocket.

- GET /runs
  - Description: List recent runs (paging optional)
  - Query: `limit: int = 50`, `offset: int = 0`, `status: Optional[str]`
  - Response: `List[RunSummary]` (200)

- GET /runs/{run_id}
  - Description: Full run detail
  - Response: `RunDetail` (200)

- POST /runs/{run_id}/cancel
  - Description: Cancel a running job
  - Response: `{ "ok": true }` (200) or 403/404
  - Auth: admin

- POST /runs/{run_id}/retry
  - Description: Retry the whole pipeline (or optionally a stage via body)
  - Body: optional `{ "stage": "Developer" }`
  - Response: `GenerateResponse` (202)
  - Auth: admin

- GET /runs/{run_id}/stages
  - Description: List per-stage statuses
  - Response: `List[StageStatus]` (200)

- GET /runs/{run_id}/stages/{stage_name}
  - Description: Get textual output for a stage (narrow preview)
  - Response: `{ "output": "...", "summary": "..." }` (200)

- POST /agents/{agent_name}/execute
  - Description: Execute a single agent node with provided input (useful for step-debugging)
  - Request: `{ "run_id": Optional[str], "input": dict }`
  - Response: `{ "output": dict, "status": "ok" }` (200)
  - Auth: admin for production systems

Group B — Artifacts & downloads

- GET /runs/{run_id}/artifacts
  - Description: List artifacts produced by a run
  - Response: `List[ArtifactEntry]` (200)

- GET /runs/{run_id}/artifacts/{path:path}
  - Description: Download or view artifact file
  - Response: `file` or plain text with `Content-Type` based on file

- GET /runs/{run_id}/download
  - Description: Stream a zip of the `output/<run_id>/` folder
  - Response: ZIP file (200)

Group C — Logs, streaming, and real-time updates

- GET /runs/{run_id}/logs
  - Description: Return paginated logs for the run
  - Query: `from: datetime`, `limit: int`
  - Response: `List[LogEntry]`

- WebSocket /ws/runs/{run_id}
  - Description: Live events for a run (stage transitions, log lines, progress)
  - Frames: JSON objects with `type` enum: `stage_update`, `log`, `artifact`, `validation_update`.
  - Usage: Frontend should open a WS for a run page and fallback to polling if unavailable.

- Server-Sent Events alternative: GET /runs/{run_id}/events (Accept: text/event-stream)

Group D — Knowledge base (RAG) management

- POST /kb/import
  - Description: Upload or reference a challenge corpus to add to RAG
  - Multipart upload or JSON with `url`/`path`.
  - Response: `KBImportResponse` (201)
  - Notes: Trigger reindexing (async)

- GET /kb
  - Description: List KB entries/examples
  - Response: `List[{ id, task_name, category, difficulty }]`

- GET /kb/{id}
  - Description: Return KB entry detail (files, content snippet)

- DELETE /kb/{id}
  - Description: Remove KB entry
  - Auth: admin

- GET /kb/search?q=...
  - Description: Search KB by keyword (simple) or vector (if implemented)

Group E — Skill templates management

- GET /skills
  - Description: List available skill templates (agent => file)
  - Response: `List[SkillFile]`

- GET /skills/{agent}
  - Description: Get skill content for agent (e.g., `architect`, `developer`)
  - Response: `SkillFile`

- PUT /skills/{agent}
  - Description: Replace/update skill content
  - Request: `{ "content": "..." }`
  - Response: `SkillFile` (200)
  - Auth: admin

- POST /skills/{agent}/reset
  - Description: Reset skill to repo default
  - Auth: admin

Group F — Settings & housekeeping

- GET /settings
  - Description: visible app settings (read-only for frontend)
  - Response: `{ model_defaults: {...}, verification_enabled: bool, ... }`

- PUT /settings
  - Description: change defaults (admin)

- GET /health (already `/` exists) keep `/health` alias
  - Response: `RootResponse`

Group G — Presets & demo helpers

- GET /presets
  - Description: list demo prompt presets with short description
  - Response: `[{ id, label, prompt_template }]`

- POST /presets/run
  - Description: run a preset by id with optional parameter overrides
  - Body: `{ "preset_id": "x", "overrides": { ... } }`

---

## Auth & rate limiting recommendations

- Add a simple API-key header (e.g., `X-API-Key`) for the frontend to use in demo mode.
- Mark admin endpoints (`/runs/*/retry`, `/runs/*/cancel`, `/kb/*/delete`, `/skills/*/write`, `/settings`) to require an admin key.
- Implement a per-IP rate limit for `/generate` to avoid runaway costs.

---

## Frontend integration notes (polling vs streaming)

- For the run page, prefer WebSocket `/ws/runs/{run_id}` to receive `stage_update` and `log` events. If WS unavailable, poll `GET /runs/{run_id}` every 2s.
- Artifact downloads: open `GET /runs/{run_id}/artifacts/{path}` directly in a new tab; for zipped download call `/runs/{run_id}/download`.
- When starting `/generate`, frontend should immediately open WS and show a skeleton pipeline with the 6 stages (`Architect`, `Storyteller`, `Developer`, `DevOps`, `Solver`, `Validator`).

Event message examples (WebSocket frame):

- Stage update

```json
{ "type": "stage_update", "run_id": "r-123", "stage": "Developer", "status": "running", "summary": "Wrote 7 files" }
```

- Log line

```json
{ "type": "log", "run_id": "r-123", "stage": "Validator", "timestamp": "2026-05-14T12:00:00Z", "level": "info", "message": "docker build succeeded" }
```

- Artifact available

```json
{ "type": "artifact", "run_id": "r-123", "path": "source/main.c", "agent": "Developer" }
```

- Validation update

```json
{ "type": "validation_update", "run_id": "r-123", "passed": true, "flag": "CTF{...}" }
```

---

## Implementation checklist (exact code tasks)

1. Extend `api/schemas.py` with the models listed above.
2. Add `api/routes/generation.py` — contains `/generate`, `/runs`, `/runs/{id}`, `/runs/{id}/download`.
3. Add `api/routes/stages.py` — contains `/runs/{id}/stages*`, `/agents/{agent}/execute`.
4. Add `api/routes/artifacts.py` — `/runs/{id}/artifacts` and file download streaming.
5. Add `api/routes/kb.py` — `/kb/*` import/search/list/delete.
6. Add `api/routes/skills.py` — `/skills/*` CRUD.
7. Add `api/routes/stream.py` — WebSocket `/ws/runs/{run_id}` and SSE fallback.
8. Wire these routers into `api/main.py` with proper `prefix` and `tags`.
9. Implement backing service layer (thin) that calls into existing orchestrator/graph manager:
   - `orchestrator.manager.start_run(prompt) -> run_id`
   - `orchestrator.manager.get_run(run_id) -> RunDetail` etc.
   If `orchestrator.manager` does not exist yet, add a thin facade in `api/services/orchestrator.py` that imports `orchestrator.manager` (or `graph.manager`) and adapts return types to the Pydantic schemas.
10. Ensure artifact file serving uses `aiofiles`/StreamingResponse for efficiency.
11. Add unit tests for endpoint request/response shapes (pytest + TestClient).
12. Add small `api/auth.py` dependency for admin-key header and include it where needed.

---

## Example curl sequences the frontend will use (exact examples)

Start a run:

```bash
curl -X POST https://api.example.com/generate \
  -H "Content-Type: application/json" \
  -d '{"mode":"intent","difficulty":"medium","category":"web","topic":"SQL injection"}'
# Response: {"run_id":"r-123","status":"accepted"}
```

Open WS for live updates (JS example):

```js
const ws = new WebSocket("wss://api.example.com/ws/runs/r-123");
ws.onmessage = (evt) => { const data = JSON.parse(evt.data); /* update UI */ };
```

Fetch run details (polling fallback):

```bash
curl https://api.example.com/runs/r-123
```

Download an artifact:

```bash
curl -o README.md https://api.example.com/runs/r-123/artifacts/README.md
```

Download zip of entire run:

```bash
curl -o r-123.zip https://api.example.com/runs/r-123/download
```

---

## UI-to-API mapping (how frontend pages use endpoints)

- New Challenge page
  - POST `/generate`
  - Immediately open WS `/ws/runs/{run_id}`
  - Poll `/runs/{run_id}` if WS not available

- Run Details page
  - GET `/runs/{run_id}` for summary
  - GET `/runs/{run_id}/stages` for stage list
  - GET `/runs/{run_id}/artifacts` to build file tree
  - GET `/runs/{run_id}/logs` to show paged logs
  - WebSocket `/ws/runs/{run_id}` for real-time updates

- Knowledge Base page
  - GET `/kb` (list)
  - POST `/kb/import` (upload corpus)
  - GET `/kb/{id}` for example detail

- Skill editor page
  - GET `/skills/{agent}`
  - PUT `/skills/{agent}` (save)

- Admin actions (guards)
  - POST `/runs/{run_id}/retry`
  - POST `/runs/{run_id}/cancel`

---

## Notes on incremental rollout (minimally viable implementation)

1. MVP: implement `POST /generate`, `GET /runs/{id}`, `GET /runs` and `GET /runs/{id}/artifacts` + simple log endpoint. Use polling only.
2. Next: add WebSocket `/ws/runs/{id}` for live updates and `/runs/{id}/download`.
3. Next: KB import/search, skills editor, admin endpoints.
4. Polish: SSE fallback, streaming build logs (docker build), zip-on-demand, pagination for runs.

---

If you want, I can now:

- generate the skeleton route files under `api/routes/` and a minimal `api/services/orchestrator.py` facade wired to `orchestrator.manager` (or placeholders if that manager function names differ), or
- add the new Pydantic models directly to `api/schemas.py` now.

Which should I do next?