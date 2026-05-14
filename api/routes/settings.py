from fastapi import APIRouter, Depends

from api.auth import admin_required

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
def put_settings(body: dict, _=Depends(admin_required)):
    _SETTINGS.update(body)
    return _SETTINGS
