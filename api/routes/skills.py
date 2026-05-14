from fastapi import APIRouter, Depends, HTTPException

from api.auth import admin_required
from api.schemas import SkillFile

router = APIRouter()

# simple in-memory skill store for now
_SKILLS = {
    'architect': SkillFile(agent='architect', content='default architect skill'),
    'developer': SkillFile(agent='developer', content='default developer skill'),
}


@router.get('/skills')
def list_skills():
    return list(_SKILLS.values())


@router.get('/skills/{agent}', response_model=SkillFile)
def get_skill(agent: str):
    s = _SKILLS.get(agent)
    if not s:
        raise HTTPException(status_code=404, detail='skill not found')
    return s


@router.put('/skills/{agent}', response_model=SkillFile)
def put_skill(agent: str, body: SkillFile, _=Depends(admin_required)):
    _SKILLS[agent] = body
    return body


@router.post('/skills/{agent}/reset')
def reset_skill(agent: str, _=Depends(admin_required)):
    _SKILLS[agent] = SkillFile(agent=agent, content='')
    return {'ok': True}
