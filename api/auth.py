from fastapi import Depends, Header, HTTPException
import os
import logging

logger = logging.getLogger(__name__)


def get_api_key(x_api_key: str = Header(None)) -> str:
    return x_api_key


def admin_required(x_api_key: str = Depends(get_api_key)):
    admin_key = os.environ.get('TOROIDBOT_ADMIN_KEY')
    if not admin_key:
        logger.warning('TOROIDBOT_ADMIN_KEY is not set; admin endpoints will reject requests')
        raise HTTPException(status_code=500, detail='admin key not configured')
    if x_api_key != admin_key:
        raise HTTPException(status_code=403, detail='admin api key required')
    return True
