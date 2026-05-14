from fastapi import APIRouter, Depends

router = APIRouter()

# Simple in-memory settings for frontend visibility
_SETTINGS = {
    "model_defaults": {"architect": "default-model"},
    "verification_enabled": True,
}


@router.get('/settings')
def get_settings():
    return _SETTINGS


@router.put('/settings')
def put_settings(body: dict, _=Depends(lambda: True)):
    _SETTINGS.update(body)
    return _SETTINGS
