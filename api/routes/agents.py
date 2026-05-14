from fastapi import APIRouter, HTTPException, Depends
from typing import Any

from api.auth import admin_required

router = APIRouter()

_AGENT_MODULES = {
    'architect': 'graph.nodes.architect_node',
    'storyteller': 'graph.nodes.storyteller_node',
    'developer': 'graph.nodes.developer_node',
    'devops': 'graph.nodes.devops_node',
    'solver': 'graph.nodes.solver_node',
    'validator': 'graph.nodes.validator_node',
}


@router.post('/agents/{agent_name}/execute')
async def execute_agent(agent_name: str, payload: dict[str, Any], _=Depends(admin_required)):
    """Execute a single agent node for debugging.

    The agent name is validated against an explicit allowlist so the endpoint
    cannot import arbitrary modules.
    """
    module_path = _AGENT_MODULES.get(agent_name)
    if not module_path:
        raise HTTPException(status_code=404, detail='agent not found')

    try:
        from importlib import import_module

        module = import_module(module_path)
        run_fn = getattr(module, 'run')
    except Exception:
        raise HTTPException(status_code=501, detail='agent execution not available in this environment')

    # Build a minimal CTFState using the payload.user_prompt if present. We
    # avoid importing agents.schemas here to keep the endpoint lightweight;
    # the node itself is responsible for Pydantic validation.
    try:
        result = await run_fn(payload)  # nodes expect a CTFState; availability varies
        return {'status': 'ok', 'output': result}
    except TypeError:
        # run_fn likely expects a CTFState; we can't construct one reliably
        raise HTTPException(status_code=400, detail='agent expects internal CTFState; execute only works in integration environments')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
