from fastapi import APIRouter, HTTPException, Depends
from typing import List

from api.schemas import KBImportResponse
from api.auth import admin_required

router = APIRouter()

# Simple in-memory KB store for dev/testing
_KB: dict[str, dict] = {}


@router.post('/kb/import', response_model=KBImportResponse)
def import_kb(item: dict, _=Depends(admin_required)):
    # Accepts JSON {"path": "/some/path"} or {"url": "..."}
    _id = f"kb-{len(_KB)+1}"
    # For now just echo back a fake count
    count = 1
    _KB[_id] = {"meta": item, "count": count}
    return KBImportResponse(id=_id, count=count)


@router.get('/kb')
def list_kb():
    return [{"id": k, "count": v["count"]} for k, v in _KB.items()]


@router.get('/kb/search')
def search_kb(q: str):
    # naive substring search over stored meta
    results = []
    for k, v in _KB.items():
        if q.lower() in str(v['meta']).lower():
            results.append({'id': k, 'meta': v['meta']})
    return results


@router.get('/kb/{kb_id}')
def get_kb(kb_id: str):
    v = _KB.get(kb_id)
    if not v:
        raise HTTPException(status_code=404, detail='kb not found')
    return v


@router.delete('/kb/{kb_id}')
def delete_kb(kb_id: str, _=Depends(admin_required)):
    if kb_id in _KB:
        del _KB[kb_id]
        return {'ok': True}
    raise HTTPException(status_code=404, detail='kb not found')
