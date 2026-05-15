import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from api.services.orchestrator import orchestrator_service

router = APIRouter()


@router.websocket('/ws/runs/{run_id}')
async def ws_run(websocket: WebSocket, run_id: str):
    await websocket.accept()
    try:
        # send current state immediately
        d = orchestrator_service.get_run(run_id)
        if not d:
            await websocket.send_json({'type': 'error', 'message': 'run not found'})
            await websocket.close()
            return
        await websocket.send_json({'type': 'init', 'summary': d.summary.model_dump(mode='json')})
        # poll and stream changes until finished
        prev_stage_state: dict[str, tuple] = {}
        while True:
            d = orchestrator_service.get_run(run_id)
            if not d:
                break
            # send stage updates
            for s in d.stages:
                stage_state = (s.name, s.status, s.summary, s.started_at, s.finished_at)
                if prev_stage_state.get(s.name) != stage_state:
                    await websocket.send_json({'type': 'stage_update', 'stage': s.name, 'status': s.status, 'summary': s.summary})
                    prev_stage_state[s.name] = stage_state
            if d.summary.status in ('succeeded', 'failed', 'cancelled'):
                await websocket.send_json({'type': 'validation_update', 'status': d.summary.status, 'validation': (d.validation.model_dump(mode='json') if d.validation else None)})
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.get('/runs/{run_id}/events')
def sse_events(run_id: str):
    """Server-Sent Events fallback for environments where WebSocket isn't available."""
    from fastapi.responses import StreamingResponse

    async def event_stream():
        prev_stage_state: dict[str, tuple] = {}
        while True:
            d = orchestrator_service.get_run(run_id)
            if not d:
                yield f"data: {json.dumps({'type': 'error', 'message': 'run not found'})}\n\n"
                break
            # stage updates
            for s in d.stages:
                stage_state = (s.name, s.status, s.summary, s.started_at, s.finished_at)
                if prev_stage_state.get(s.name) != stage_state:
                    payload = {"type": "stage_update", "stage": s.name, "status": s.status, "summary": s.summary}
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                    prev_stage_state[s.name] = stage_state
            if d.summary.status in ('succeeded', 'failed', 'cancelled'):
                payload = {"type": "validation_update", "status": d.summary.status, "validation": (d.validation.model_dump(mode='json') if d.validation else None)}
                yield f"data: {json.dumps(payload, default=str)}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type='text/event-stream')
