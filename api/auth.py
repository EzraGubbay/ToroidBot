from fastapi import Depends, Header, HTTPException
import os


def get_api_key(x_api_key: str = Header(None)) -> str:
    return x_api_key


def admin_required(x_api_key: str = Depends(get_api_key)):
    admin_key = os.environ.get('TOROIDBOT_ADMIN_KEY', 'dev-admin-key')
    if x_api_key != admin_key:
        raise HTTPException(status_code=403, detail='admin api key required')
    return True
