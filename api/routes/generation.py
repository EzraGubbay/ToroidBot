from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from api.schemas import GenerateResponse, PromptRequest
from api.services.orchestrator import orchestrator_service

router = APIRouter()


@router.post('/generate', response_model=GenerateResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate(prompt: PromptRequest):
    # start a run and return run id
    run_id = orchestrator_service.start_run(prompt.dict())
    return GenerateResponse(run_id=run_id, status='accepted', message='Run scheduled')
