from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Import FastMCP
from mcp.server.fastmcp import FastMCP

from agents.event_config import load_event_config
from agents.schemas import CTFState
from graph.pipeline import run_pipeline
from orchestrator.output import save_challenge

load_dotenv()

# Initialize FastMCP (defaults to stdio routing)
mcp = FastMCP("ToroidBot")

# The @mcp.tool decorator automatically uses type hints to generate the schema
@mcp.tool()
async def generate_challenge(
    prompt: str,
    config_path: Optional[str] = None,
    no_sandbox: bool = False
) -> str:
    """Generate a new CTF challenge based on a natural language prompt.
    
    Args:
        prompt: The challenge concept, e.g. "Medium web SQLi challenge".
        config_path: Optional absolute path to an event config (JSON/YAML).
        no_sandbox: If true, skips the Docker Validator sandbox.
    """
    event = None
    if config_path:
        event = load_event_config(Path(config_path))
    
    state = CTFState(user_prompt=prompt, event=event)
    
    if event:
        state.max_retries = event.max_retries
        state.use_sandbox = False if no_sandbox else event.use_sandbox
    else:
        state.use_sandbox = not no_sandbox

    try:
        # Run the ToroidBot pipeline
        state = await run_pipeline(state)

        if state.validation and state.validation.passed:
            output_dir = save_challenge(state)
            return (
                f"Challenge generated successfully!\n"
                f"Saved to: {output_dir}\n"
                f"Manifest:\n{state.manifest.model_dump_json(indent=2)}"
            )
        else:
            errors = "\n".join(state.validation.errors) if state.validation else "Unknown failure"
            return f"Challenge generation failed validation.\nErrors:\n{errors}"

    except Exception as e:
        import logging
        logging.exception("Internal error during challenge generation")
        return f"An internal error occurred: {str(e)}"

if __name__ == "__main__":
    # FastMCP automatically starts the stdio server when run directly.
    # To use SSE, you would configure transport="sse".
    mcp.run()
