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

# Initialize FastMCP (handles the stdio/sse routing for you)
mcp = FastMCP("ToroidBot")

# We can define a Pydantic model for our tool's arguments
class GenerateChallengeArgs(BaseModel):
    prompt: str = Field(
        ..., 
        description='The challenge concept, e.g. "Medium web SQLi challenge"'
    )
    config_path: Optional[str] = Field(
        default=None, 
        description="Optional absolute path to an event config (JSON/YAML)."
    )
    no_sandbox: bool = Field(
        default=False, 
        description="If true, skips the Docker Validator sandbox."
    )

# The @mcp.tool decorator automatically uses Pydantic to generate the schema
@mcp.tool()
async def generate_challenge(args: GenerateChallengeArgs) -> str:
    """Generate a new CTF challenge based on a natural language prompt."""
    
    event = None
    if args.config_path:
        event = load_event_config(Path(args.config_path))
    
    state = CTFState(user_prompt=args.prompt, event=event)
    state.max_retries = event.max_retries if event else 3
    state.use_sandbox = False if args.no_sandbox else (event.use_sandbox if event else True)

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
        return f"An internal error occurred: {str(e)}"

if __name__ == "__main__":
    # FastMCP automatically starts the stdio server when run directly
    mcp.run()
