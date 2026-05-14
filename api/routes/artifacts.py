from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse
from pathlib import Path

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
            # if the orchestrator stored an absolute output_dir, try to serve
            r = orchestrator_service.runs.get(run_id)
            out = r.get('output_dir') if r else None
            if out:
                base = Path(out).resolve()
                p = (base / path).resolve()
                if base in p.parents or p == base:
                    if p.exists() and p.is_file():
                        return FileResponse(p, media_type='application/octet-stream')
            # fallback placeholder
            return PlainTextResponse(f"// artifact: {a.path}\n// produced by {a.agent}\n")
    raise HTTPException(status_code=404, detail='artifact not found')
