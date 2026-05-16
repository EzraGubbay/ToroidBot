import logging
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Import FastMCP
from mcp.server.fastmcp import FastMCP, Context

from agents.event_config import load_event_config
from agents.schemas import CTFState
from graph.pipeline import run_pipeline
from orchestrator.output import save_challenge

load_dotenv()

# Configure logging to write to stderr so it doesn't interfere with the MCP protocol on stdout
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("toroidbot-mcp")

# Initialize FastMCP (defaults to stdio routing)
mcp = FastMCP("ToroidBot")

# The @mcp.tool decorator automatically uses type hints to generate the schema
@mcp.tool()
async def generate_challenge(
    prompt: str,
    ctx: Context,
    config_path: Optional[str] = None,
    no_sandbox: bool = False,
    model: str = "openrouter:anthropic/claude-sonnet-4"
) -> str:
    """Generate a new CTF challenge based on a natural language prompt.
    
    Args:
        prompt: The challenge concept, e.g. "Medium web SQLi challenge".
        ctx: MCP context for logging and progress.
        config_path: Optional absolute path to an event config (JSON/YAML).
        no_sandbox: If true, skips the Docker Validator sandbox.
        model: Model string to use for generation (defaults to Claude 4 Sonnet).
    """
    event = None
    if config_path:
        ctx.info(f"Loading event config from: {config_path}")
        event = load_event_config(Path(config_path))
    
    state = CTFState(user_prompt=prompt, event=event, model=model)
    
    if event:
        state.max_retries = event.max_retries
        state.use_sandbox = False if no_sandbox else event.use_sandbox
    else:
        state.use_sandbox = not no_sandbox

    ctx.info(f"Starting pipeline for prompt: {prompt}")
    ctx.info(f"Model: {model}, Sandbox: {state.use_sandbox}")

    try:
        # Run the ToroidBot pipeline
        state = await run_pipeline(state)

        if state.validation and state.validation.passed:
            output_dir = save_challenge(state)
            ctx.info(f"Challenge generated successfully at: {output_dir}")
            return (
                f"Challenge generated successfully!\n"
                f"Saved to: {output_dir}\n"
                f"Manifest:\n{state.manifest.model_dump_json(indent=2)}"
            )
        else:
            errors = "\n".join(state.validation.errors) if state.validation else "Unknown failure"
            ctx.error(f"Challenge generation failed validation: {errors}")
            return f"Challenge generation failed validation.\nErrors:\n{errors}"

    except Exception as e:
        logger.exception("Internal error during challenge generation")
        ctx.error(f"Internal error: {str(e)}")
        return f"An internal error occurred: {str(e)}"

if __name__ == "__main__":
    # FastMCP automatically starts the stdio server when run directly.
    mcp.run()
