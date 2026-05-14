from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from api.services.orchestrator import orchestrator_service

router = APIRouter()


@router.get('/runs/{run_id}/artifacts')
def list_artifacts(run_id: str):
    return orchestrator_service.list_artifacts(run_id)


@router.get('/runs/{run_id}/artifacts/{path:path}')
def get_artifact(run_id: str, path: str):
    arts = orchestrator_service.list_artifacts(run_id)
    for a in arts:
        if a.path == path:
            # return a small placeholder text for now
            return PlainTextResponse(f"// artifact: {a.path}\n// produced by {a.agent}\n")
    raise HTTPException(status_code=404, detail='artifact not found')
