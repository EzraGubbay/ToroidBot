import asyncio
import io
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4
import os
import threading

from api.schemas import (
    ArtifactEntry,
    LogEntry,
    RunDetail,
    RunSummary,
    StageStatus,
    ValidationResult,
)

# Try to import the real orchestrator pipeline and output saver. If the
# heavyweight dependencies are unavailable (tests or dev without env), fall
# back to the in-memory simulator implemented below. Additionally gate the
# real orchestrator with the `USE_REAL_ORCHESTRATOR` env var so CI/dev can
# opt into running the full pipeline explicitly.
try:
    from graph.pipeline import run_pipeline  # async
    from orchestrator.output import save_challenge
    from agents.schemas import CTFState
    from agents.event_config import EventConfig
    _imports_ok = True
except Exception:
    run_pipeline = None
    save_challenge = None
    CTFState = None
    EventConfig = None
    _imports_ok = False

# Env toggle: set USE_REAL_ORCHESTRATOR=1, true, or yes (case-insensitive)
_env_toggle = os.getenv("USE_REAL_ORCHESTRATOR", "0").lower() in ("1", "true", "yes")
REAL_ORCHESTRATOR_AVAILABLE = bool(_imports_ok and _env_toggle)


class OrchestratorService:
    def __init__(self):
        # in-memory store for runs and a simple fallback simulator
        self.runs: Dict[str, Dict] = {}

    def start_run(self, prompt: dict) -> str:
        """
        Start a run. If the real orchestrator is available, schedule the full
        pipeline; otherwise run the lightweight simulator for frontend/tests.
        """
        run_id = f"r-{uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        stages = [
            StageStatus(name=s, status='queued')
            for s in ('Architect', 'Storyteller', 'Developer', 'DevOps', 'Solver', 'Validator')
        ]
        self.runs[run_id] = {
            'summary': RunSummary(
                run_id=run_id,
                prompt=prompt,
                status='pending',
                started_at=now,
                current_stage=None,
            ),
            'stages': stages,
            'artifacts': [],
            'logs': [],
            'validation': None,
        }

        if REAL_ORCHESTRATOR_AVAILABLE:
            # Build a CTFState from the incoming prompt dict. Use EventConfig if
            # caller supplied an `event` object; otherwise leave event unset.
            try:
                user_prompt = prompt.get('topic') or prompt.get('mode') or prompt.get('cve') or 'Generate CTF challenge'
                state = CTFState(user_prompt=user_prompt)
                # optional model override
                if prompt.get('model'):
                    state.set_cli_model_override(prompt.get('model'))
                if prompt.get('event') and isinstance(prompt.get('event'), dict) and EventConfig is not None:
                    try:
                        state.event = EventConfig(**prompt.get('event'))
                    except Exception:
                        # invalid event payload — log and continue without event
                        self._log(run_id, 'warning', 'Invalid event payload provided; ignoring')

                # schedule the real pipeline in background
                self._schedule_background(self._run_pipeline_and_record(run_id, state))
            except Exception as e:
                # If anything goes wrong while scheduling the real run, fall
                # back to the simulator so the API remains responsive.
                self._log(run_id, 'error', f'Failed to schedule real pipeline: {e}; falling back to simulator')
                self._schedule_background(self._simulate_run(run_id))
        else:
            # schedule simulator
            self._schedule_background(self._simulate_run(run_id))

        return run_id

    def _schedule_background(self, coro):
        """Schedule a coroutine on the running loop when possible.

        If called from a synchronous context without a running loop, fall back
        to a dedicated daemon thread so tests/direct usage don't crash.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            thread = threading.Thread(target=lambda: asyncio.run(coro), daemon=True)
            thread.start()
            return
        loop.create_task(coro)

    async def _run_pipeline_and_record(self, run_id: str, state):
        """Run the real graph.pipeline.run_pipeline and record outputs to the
        in-memory store. This function is scheduled as a background task.
        """
        store = self.runs.get(run_id)
        if not store:
            return
        store['summary'].status = 'running'
        store['summary'].current_stage = 'Architect'
        self._log(run_id, 'info', 'Pipeline started (real orchestrator)')
        try:
            result_state = await run_pipeline(state)
        except Exception as e:
            store['summary'].status = 'failed'
            store['summary'].finished_at = datetime.now(timezone.utc)
            self._log(run_id, 'error', f'Pipeline failed: {e}')
            store['validation'] = ValidationResult(passed=False, error=str(e))
            return

        # On success, save challenge outputs and list artifacts from the saved
        # output directory if possible.
        if result_state.validation and result_state.validation.passed:
            try:
                out_dir = save_challenge(result_state)
                # Walk output dir to populate artifacts
                artifacts: List[ArtifactEntry] = []
                for p in out_dir.rglob('*'):
                    if p.is_file():
                        rel = str(p.relative_to(out_dir))
                        artifacts.append(ArtifactEntry(path=rel, role='file', agent='pipeline', size=p.stat().st_size))
                store['artifacts'] = artifacts
                # record absolute output dir for artifact serving
                try:
                    store['output_dir'] = out_dir
                except Exception:
                    pass
            except Exception as e:
                self._log(run_id, 'warning', f'Failed to save challenge outputs: {e}')

        store['validation'] = ValidationResult(passed=bool(getattr(result_state, 'validation', None) and result_state.validation.passed), flag=(result_state.manifest.flag if result_state.manifest else None), logs=[])
        store['summary'].status = 'succeeded' if store['validation'].passed else 'failed'
        store['summary'].finished_at = datetime.now(timezone.utc)
        self._log(run_id, 'info', 'Pipeline completed')

    async def _simulate_run(self, run_id: str):
        # simple progression sim (used when real orchestrator not available)
        store = self.runs.get(run_id)
        if not store:
            return
        store['summary'].status = 'running'
        for stage in store['stages']:
            stage.status = 'running'
            store['summary'].current_stage = stage.name
            self._log(run_id, 'info', f"{stage.name} started")
            await asyncio.sleep(0.5)
            # create fake artifact for developer
            if stage.name == 'Developer':
                a = ArtifactEntry(path='source/main.c', role='source', agent='Developer', size=1234)
                store['artifacts'].append(a)
                self._log(run_id, 'info', 'Developer wrote source/main.c')
            stage.status = 'passed'
            stage.finished_at = datetime.now(timezone.utc)
            await asyncio.sleep(0.1)
        # validation
        store['validation'] = ValidationResult(passed=True, flag='CTF{simulated_flag}', logs=[])
        store['summary'].status = 'succeeded'
        store['summary'].finished_at = datetime.now(timezone.utc)
        self._log(run_id, 'info', 'Run completed')

    def _log(self, run_id: str, level: str, message: str):
        entry = LogEntry(timestamp=datetime.now(timezone.utc), level=level, message=message)
        self.runs[run_id]['logs'].append(entry)

    def get_run(self, run_id: str) -> Optional[RunDetail]:
        r = self.runs.get(run_id)
        if not r:
            return None
        return RunDetail(
            summary=r['summary'],
            stages=r['stages'],
            artifacts=r['artifacts'],
            validation=r.get('validation'),
        )

    def list_runs(self, limit: int = 50, offset: int = 0) -> List[RunSummary]:
        items = [v['summary'] for v in self.runs.values()]
        return items[offset: offset + limit]

    def cancel_run(self, run_id: str) -> bool:
        r = self.runs.get(run_id)
        if not r:
            return False
        r['summary'].status = 'cancelled'
        self._log(run_id, 'info', 'Cancelled by user')
        return True

    def retry_run(self, run_id: str) -> Optional[str]:
        # create a new run copying prompt
        r = self.runs.get(run_id)
        if not r:
            return None
        prompt = r['summary'].prompt
        # prompt may be a model instance or dict
        if hasattr(prompt, 'model_dump'):
            prompt = prompt.model_dump()
        elif not isinstance(prompt, dict):
            prompt = dict(prompt)
        return self.start_run(prompt)

    def list_artifacts(self, run_id: str) -> List[ArtifactEntry]:
        r = self.runs.get(run_id)
        if not r:
            return []
        return r['artifacts']

    def emit_event(self, run_id: str, event: dict):
        # debug helper: push event into logs
        self._log(run_id, 'debug', f"user-event: {event}")

    def download_run_zip(self, run_id: str) -> bytes:
        r = self.runs.get(run_id)
        if not r:
            raise FileNotFoundError()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            # include files from artifacts when available
            for a in r['artifacts']:
                try:
                    z.writestr(a.path, f"// generated by {a.agent} (size={a.size})\n")
                except Exception:
                    pass
            z.writestr('README.md', f"Run: {run_id}\nStatus: {r['summary'].status}\n")
        buf.seek(0)
        return buf.read()


orchestrator_service = OrchestratorService()
