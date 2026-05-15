# ToroidBot Frontend API Reference

This document describes the FastAPI endpoints currently available for the frontend. It is written for an AI agent that needs to build UI screens, data fetching, and live run progress views.

## Conventions

- Base URL: same origin as the API server.
- JSON is used everywhere except downloads and streaming.
- Long-running work starts with `POST /generate` and is followed by polling, WebSocket streaming, or SSE.
- Admin-only routes require `X-API-Key` with the configured admin key.
- Some endpoints are intentionally backed by in-memory stores in dev mode (`/kb`, `/skills`, `/settings`, `/presets`). Treat them as mutable demo state, not persistent storage.

## Core Models Returned by the API

- `RootResponse`: `{ service, version, status }`
- `GenerateResponse`: `{ run_id, status, message? }`
- `RunSummary`: `{ run_id, prompt?, status, started_at?, finished_at?, current_stage? }`
- `StageStatus`: `{ name, status, summary?, started_at?, finished_at? }`
- `ArtifactEntry`: `{ path, role, agent, size? }`
- `LogEntry`: `{ timestamp, level, message }`
- `ValidationResult`: `{ passed, flag?, logs?, error? }`
- `RunDetail`: `{ summary, stages, artifacts, validation? }`
- `KBImportResponse`: `{ id, count }`
- `SkillFile`: `{ agent, content, sha256? }`

## Endpoints

### Health / app root

#### `GET /`
- Auth: none
- Purpose: simple liveness check.
- Returns: `RootResponse`
- Example response:
  ```json
  { "service": "toroidbot-api", "version": "0.1.0", "status": "ok" }
  ```

### Generation / run lifecycle

#### `POST /generate`
- Auth: none in current implementation
- Purpose: start a new generation run.
- Request body: `PromptRequest`
  ```json
  {
    "mode": "intent",
    "difficulty": "medium",
    "category": "web",
    "topic": "sql injection",
    "cve": null,
    "constraints": { "language": "python" },
    "model": "google-gla:gemini-2.5-flash",
    "event": null
  }
  ```
- Returns: `GenerateResponse` with HTTP `202 Accepted`
- Frontend use: create the run card immediately, open the live stream, and begin polling details.

#### `GET /runs`
- Auth: none
- Query params: `limit` default `50`, `offset` default `0`
- Purpose: list recent runs.
- Returns: `RunSummary[]`
- Frontend use: dashboard list, run history, filtering/pagination.

#### `GET /runs/{run_id}`
- Auth: none
- Purpose: fetch full run detail.
- Returns: `RunDetail`
- Frontend use: run detail page, stage timeline, artifact panel, validation summary.
- Error: `404 run not found`

#### `POST /runs/{run_id}/cancel`
- Auth: admin (`X-API-Key`)
- Purpose: cancel a running job.
- Returns: `{ "ok": true }`
- Errors: `404 run not found`
- Frontend use: stop button on the run page.

#### `POST /runs/{run_id}/retry`
- Auth: admin (`X-API-Key`)
- Purpose: retry the whole pipeline for a run.
- Returns: `{ "run_id": "new-run-id" }`
- Errors: `404 run not found`
- Frontend use: retry button when a run fails.

#### `GET /runs/{run_id}/stages`
- Auth: none
- Purpose: fetch just the stage list for a run.
- Returns: `StageStatus[]`
- Frontend use: lightweight refresh of the stage timeline.

#### `GET /runs/{run_id}/logs`
- Auth: none
- Query params: `limit` default `200`
- Purpose: return the newest log entries for a run.
- Returns: `LogEntry[]`
- Frontend use: console/log viewer in the run detail page.

#### `GET /runs/{run_id}/download`
- Auth: none
- Purpose: download a zip archive of the run output folder.
- Returns: ZIP file with `Content-Disposition: attachment; filename="{run_id}.zip"`
- Frontend use: export/download button.

### Streaming / live progress

#### `WebSocket /ws/runs/{run_id}`
- Auth: none
- Purpose: live updates for a run.
- Returns: JSON frames.
- Frames currently sent:
  - `init`: initial `summary`
  - `stage_update`: stage name, status, summary
  - `validation_update`: final status + validation payload
  - `error`: only if the run does not exist
- Frontend use: preferred live transport for the run page.
- Notes: the server sends stage updates only when a stage changes, then sends a final validation frame when the run ends.

#### `GET /runs/{run_id}/events`
- Auth: none
- Purpose: Server-Sent Events fallback when WebSocket is not available.
- Returns: `text/event-stream`
- Event payloads currently sent:
  - `stage_update`
  - `validation_update`
  - `error` if the run does not exist
- Frontend use: browser environments or proxies where WebSocket is blocked.

### Artifacts

#### `GET /runs/{run_id}/artifacts`
- Auth: none
- Purpose: list artifacts produced by a run.
- Returns: `ArtifactEntry[]`
- Frontend use: artifact list in the run detail page.

#### `GET /runs/{run_id}/artifacts/{path:path}`
- Auth: none
- Purpose: fetch a specific artifact file.
- Returns:
  - `FileResponse` if the file exists in the stored output directory
  - otherwise a plain text placeholder with the artifact path and agent
- Frontend use: file preview pane or direct file download/open.
- Error: `404 artifact not found`

### Knowledge base (RAG) management

#### `POST /kb/import`
- Auth: admin (`X-API-Key`)
- Purpose: import a KB entry for retrieval/testing.
- Request body: arbitrary JSON, typically `{ "path": "..." }` or `{ "url": "..." }`
- Returns: `KBImportResponse`
- Frontend use: admin import form.
- Notes: current implementation stores the payload in memory and returns a synthetic `kb-xxxxxxxx` id.

#### `GET /kb`
- Auth: none
- Purpose: list KB entries.
- Returns: array of `{ id, count }`
- Frontend use: KB browser/list page.

#### `GET /kb/search?q=...`
- Auth: none
- Purpose: substring search over imported KB metadata.
- Returns: array of matching entries with `{ id, meta }`
- Frontend use: search box / filtered KB picker.

#### `GET /kb/{kb_id}`
- Auth: none
- Purpose: fetch one KB record.
- Returns: stored KB object `{ meta, count }`
- Errors: `404 kb not found`

#### `DELETE /kb/{kb_id}`
- Auth: admin (`X-API-Key`)
- Purpose: remove a KB record.
- Returns: `{ "ok": true }`
- Errors: `404 kb not found`

### Agent skill templates

#### `GET /skills`
- Auth: none
- Purpose: list all skill templates currently available.
- Returns: `SkillFile[]`
- Frontend use: skills management page.

#### `GET /skills/{agent}`
- Auth: none
- Purpose: fetch one skill template.
- Returns: `SkillFile`
- Errors: `404 skill not found`

#### `PUT /skills/{agent}`
- Auth: admin (`X-API-Key`)
- Purpose: replace a skill template.
- Request body: `SkillFile`
- Returns: `SkillFile`
- Frontend use: skill editor save action.

#### `POST /skills/{agent}/reset`
- Auth: admin (`X-API-Key`)
- Purpose: reset a skill to an empty/default value.
- Returns: `{ "ok": true }`
- Frontend use: reset button in the editor.

### Settings

#### `GET /settings`
- Auth: none
- Purpose: read app settings shown to the frontend.
- Returns: JSON object like:
  ```json
  {
    "model_defaults": { "architect": "default-model" },
    "verification_enabled": true
  }
  ```
- Frontend use: settings panel.

#### `PUT /settings`
- Auth: admin (`X-API-Key`)
- Purpose: update app settings.
- Request body: arbitrary JSON merge payload.
- Returns: updated settings object.
- Frontend use: save button in settings panel.

### Presets

#### `GET /presets`
- Auth: none
- Purpose: list demo presets.
- Returns: array of `{ id, label, prompt_template }`
- Frontend use: preset picker for quick starts.

#### `POST /presets/run`
- Auth: none
- Purpose: invoke a preset by id.
- Request body:
  ```json
  {
    "preset_id": "preset-1",
    "overrides": {}
  }
  ```
- Returns: `{ "status": "ok", "preset_id": "...", "overrides": { ... } }`
- Notes: current implementation does not directly start generation; it returns a status wrapper for the frontend to turn into a `/generate` call.

### Debug / dev-only helpers

#### `GET /debug/state`
- Auth: admin (`X-API-Key`)
- Purpose: expose internal run IDs for debugging.
- Returns: `{ "runs": ["..."] }`
- Frontend use: internal dev tooling only.

#### `POST /debug/runs/{run_id}/emit`
- Auth: admin (`X-API-Key`)
- Purpose: inject a debug event into a run.
- Request body: arbitrary JSON event.
- Returns: `{ "ok": true }`
- Errors: `404 run not found`
- Frontend use: test-only debug control.

## Frontend usage notes

- Start a run with `POST /generate`, then immediately open the WebSocket or SSE stream.
- Use `GET /runs/{run_id}` for the canonical run state and `GET /runs/{run_id}/logs` for the log panel.
- Show artifacts as soon as `/runs/{run_id}/artifacts` returns entries.
- For admin features, wire `X-API-Key` into your fetch client.
- The KB, skills, settings, and presets routes are currently backed by in-memory stores in dev mode, so refreshing the process resets them.
- The WebSocket is the preferred live transport; SSE is the fallback.

## Recommended frontend screens

- Home / dashboard: `/runs`, `/presets`, `/settings`
- Run detail: `/runs/{run_id}`, `/ws/runs/{run_id}`, `/runs/{run_id}/logs`, `/runs/{run_id}/artifacts`
- KB browser: `/kb`, `/kb/search`, `/kb/{kb_id}`
- Skill editor: `/skills`, `/skills/{agent}`
- Admin debug tools: `/debug/state`, `/debug/runs/{run_id}/emit`
