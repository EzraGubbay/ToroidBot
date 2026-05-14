import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from typing import Dict

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
        await websocket.send_json({'type': 'init', 'summary': d.summary.dict()})
        # poll and stream changes until finished
        prev_stage = None
        while True:
            d = orchestrator_service.get_run(run_id)
            if not d:
                break
            # send stage updates
            for s in d.stages:
                if prev_stage != s.name or s.status == 'running':
                    await websocket.send_json({'type': 'stage_update', 'stage': s.name, 'status': s.status, 'summary': s.summary})
                    prev_stage = s.name
            if d.summary.status in ('succeeded', 'failed', 'cancelled'):
                await websocket.send_json({'type': 'validation_update', 'status': d.summary.status, 'validation': (d.validation.dict() if d.validation else None)})
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

    def event_stream():
        prev_stage = None
        while True:
            d = orchestrator_service.get_run(run_id)
            if not d:
                yield f"data: { {'type': 'error', 'message': 'run not found'} }\n\n"
                break
            # stage updates
            for s in d.stages:
                if prev_stage != s.name or s.status == 'running':
                    payload = {"type": "stage_update", "stage": s.name, "status": s.status, "summary": s.summary}
                    yield f"data: {payload}\n\n"
                    prev_stage = s.name
            if d.summary.status in ('succeeded', 'failed', 'cancelled'):
                payload = {"type": "validation_update", "status": d.summary.status, "validation": (d.validation.dict() if d.validation else None)}
                yield f"data: {payload}\n\n"
                break
            import time

            time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type='text/event-stream')
