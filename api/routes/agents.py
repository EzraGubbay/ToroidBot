from fastapi import APIRouter, HTTPException, Depends
from typing import Any

router = APIRouter()


@router.post('/agents/{agent_name}/execute')
async def execute_agent(agent_name: str, payload: dict[str, Any], _=Depends(lambda: True)):
    """Execute a single agent node for debugging.

    Currently this is a thin passthrough that attempts to locate a node
    function in `graph.nodes` by convention (e.g. `architect_node.run`). If
    the runtime doesn't have the graph nodes available this returns 501.
    """
    try:
        module = __import__(f'graph.nodes.{agent_name}_node', fromlist=['run'])
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
