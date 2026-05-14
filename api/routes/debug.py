import os

from fastapi import APIRouter, HTTPException, Depends

from api.auth import admin_required
from api.services.orchestrator import orchestrator_service

router = APIRouter()


@router.get('/debug/state')
def debug_state(_=Depends(admin_required)):
    # expose internal store for debugging (dev only)
    return { 'runs': list(orchestrator_service.runs.keys()) }


@router.post('/debug/runs/{run_id}/emit')
def emit_event(run_id: str, event: dict, _=Depends(admin_required)):
    if run_id not in orchestrator_service.runs:
        raise HTTPException(status_code=404, detail='run not found')
    orchestrator_service.emit_event(run_id, event)
    return {'ok': True}
