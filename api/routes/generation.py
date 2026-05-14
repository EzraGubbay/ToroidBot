from fastapi import APIRouter, status

from api.schemas import GenerateResponse, PromptRequest
from api.services.orchestrator import orchestrator_service

router = APIRouter()


@router.post('/generate', response_model=GenerateResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate(prompt: PromptRequest):
    # start a run and return run id
    run_id = orchestrator_service.start_run(prompt.model_dump())
    return GenerateResponse(run_id=run_id, status='accepted', message='Run scheduled')
