from fastapi import APIRouter, HTTPException

router = APIRouter()

# Example in-memory presets
_PRESETS = {
    "preset-1": {"label": "Easy Web XSS", "prompt_template": "Create an easy web challenge about XSS"}
}


@router.get('/presets')
def list_presets():
    return [{"id": k, **v} for k, v in _PRESETS.items()]


@router.post('/presets/run')
def run_preset(body: dict):
    preset_id = body.get('preset_id')
    overrides = body.get('overrides', {})
    p = _PRESETS.get(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail='preset not found')
    # Build a request body for /generate; frontend should call /generate directly.
    return {"status": "ok", "preset_id": preset_id, "overrides": overrides}
