from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from typing import List

from api.schemas import RunDetail, RunSummary, StageStatus, ArtifactEntry, LogEntry
from api.services.orchestrator import orchestrator_service
from api.auth import admin_required

router = APIRouter()


@router.get('/runs', response_model=List[RunSummary])
def list_runs(limit: int = 50, offset: int = 0):
    return orchestrator_service.list_runs(limit=limit, offset=offset)


@router.get('/runs/{run_id}', response_model=RunDetail)
def get_run(run_id: str):
    d = orchestrator_service.get_run(run_id)
    if not d:
        raise HTTPException(status_code=404, detail='run not found')
    return d


@router.post('/runs/{run_id}/cancel')
def cancel_run(run_id: str, _=Depends(admin_required)):
    ok = orchestrator_service.cancel_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail='run not found')
    return {'ok': True}


@router.post('/runs/{run_id}/retry', response_model=dict)
def retry_run(run_id: str, _=Depends(admin_required)):
    new = orchestrator_service.retry_run(run_id)
    if not new:
        raise HTTPException(status_code=404, detail='run not found')
    return {'run_id': new}


@router.get('/runs/{run_id}/stages', response_model=List[StageStatus])
def list_stages(run_id: str):
    d = orchestrator_service.get_run(run_id)
    if not d:
        raise HTTPException(status_code=404, detail='run not found')
    return d.stages


@router.get('/runs/{run_id}/logs', response_model=List[LogEntry])
def get_logs(run_id: str, limit: int = 200):
    r = orchestrator_service.runs.get(run_id)
    if not r:
        raise HTTPException(status_code=404, detail='run not found')
    return r['logs'][-limit:]


@router.get('/runs/{run_id}/download')
def download_run(run_id: str):
    try:
        data = orchestrator_service.download_run_zip(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='run not found')
    # stream via StreamingResponse
    from fastapi.responses import StreamingResponse
    return StreamingResponse(iter([data]), media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{run_id}.zip"'})
