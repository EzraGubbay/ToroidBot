## Re-review: feat: REST API (FastAPI + SQLAlchemy + Pydantic)

Fresh review of the current diff (+1336 / -0). Many issues from the previous review have been addressed — nice work. Here's what remains and what's new.

---

### Previously flagged — now fixed

- ~~Hardcoded fallback admin key~~ — now raises 500 if `TOROIDBOT_ADMIN_KEY` unset
- ~~`__import__` injection~~ — now uses an allowlist + `importlib.import_module`
- ~~Settings PUT missing auth~~ — now uses `admin_required`
- ~~KB route ordering~~ — `/kb/search` now correctly above `/kb/{kb_id}`
- ~~SSE sync `time.sleep`~~ — now async generator with `await asyncio.sleep`
- ~~SSE Python repr instead of JSON~~ — now uses `json.dumps`
- ~~`.dict()` deprecated~~ — migrated to `.model_dump()`
- ~~WebSocket stage tracking~~ — now tracks full state tuple
- ~~`asyncio.create_task` fragility~~ — `_schedule_background` with thread fallback
- ~~Missing `__init__.py`~~ — added for `api/routes/` and `api/services/`
- ~~Presets error as 200~~ — now `HTTPException(404)`
- ~~Path traversal~~ — now resolves and checks path is within base dir

---

### Remaining issues

#### Security

1. **Debug endpoints are unprotected** (`api/routes/debug.py`): `/debug/state` exposes all run IDs and `/debug/runs/{run_id}/emit` lets anyone inject events — neither requires auth. These should either use `admin_required` or be gated behind a `DEBUG` env flag.

2. **CORS `allow_origins=['*']`** (`api/main.py:29`): Still wide open. Add a `# TODO: restrict in production` comment at minimum so it doesn't get forgotten.

#### Correctness

3. **WebSocket tracks only one `prev_stage_state` across all stages** (`api/routes/stream.py:23`): The single `prev_stage_state` variable is overwritten as you iterate through stages, so only the *last* stage's state is tracked. On the next poll cycle, earlier stages that haven't changed will still be re-sent. Use a `dict[str, tuple]` keyed by stage name instead.

4. **Same issue in SSE** (`api/routes/stream.py:52`): Identical bug in the SSE generator.

5. **KB ID generation will collide after deletions** (`api/routes/kb.py:16`): `f"kb-{len(_KB)+1}"` produces duplicate IDs if entries are deleted then new ones added. Use a counter or UUID instead.

6. **`cancel_run` doesn't stop the running coroutine** (`api/services/orchestrator.py`): It sets the status to `cancelled` but the simulator/pipeline task keeps running in the background. The stages will overwrite the status back to `succeeded` when they finish. Consider storing the `asyncio.Task` and calling `.cancel()`, or checking status inside the loop.

7. **Unused imports** (`api/routes/generation.py:1`): `BackgroundTasks`, `HTTPException`, and `JSONResponse` are imported but never used.

#### Housekeeping

8. **PR title still says "SQLAlchemy"** — there is no SQLAlchemy in this diff.

9. **Chat-artifact markdown files still in the diff**: `Agent_description-22_41.md` has an unfinished "What to ask about: 1. 2. 3." section and a timestamped filename. `API_ENDPOINTS_PLAN.md` ends with "Which should I do next?" — these read as conversation artifacts, not repo documentation. Move to `docs/` or remove from the PR.

10. **`time.sleep(1.0)` in tests** (`tests/test_api_endpoints.py:34`): Still fragile for CI. Could flake on slow runners. Consider polling in a loop with a timeout.

11. **Test plan in PR description is still empty**: `[ ] To be filled in as work lands.` — now that there are 5 test functions, this should be updated.

---

### Summary

The codebase is in much better shape. The security and correctness items from the first review have been addressed. Remaining blockers before leaving draft:

- **Fix**: Debug endpoint auth (#1), WebSocket/SSE per-stage tracking (#3-4), cancel not actually stopping runs (#6)
- **Clean up**: PR title (#8), chat-artifact files (#9), unused imports (#7)
- **Minor**: KB ID collisions (#5), flaky test sleep (#10), empty test plan (#11), CORS comment (#2)

🤖 Generated with [Claude Code](https://claude.com/claude-code)