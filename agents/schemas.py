"""Pydantic schemas for the CTF challenge generation pipeline.

Each agent produces one of these models. They compose into CTFState,
which is the full pipeline state passed between nodes.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

if TYPE_CHECKING:
    from agents.event_config import EventConfig

_VALID_AGENT_NAMES = {
    "architect", "storyteller", "developer", "devops", "solver", "validator",
}

# Safe Docker image tag / container name component: lowercase alphanumerics and hyphens,
# must start with an alphanumeric, no double hyphens at edges. Constrains LLM output so
# `docker run --name <manifest.name>` and `docker build -t ctf-poc/<manifest.name>` are safe.
CHALLENGE_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"


class Category(str, Enum):
    WEB = "web"
    PWN = "pwn"
    REV = "rev"
    CRYPTO = "crypto"
    MISC = "misc"
    FORENSICS = "forensics"


class RetryTarget(str, Enum):
    """Which agent the Validator wants re-run on failure."""

    DEVELOPER = "developer"
    SOLVER = "solver"


class ChallengeManifest(BaseModel):
    """Output of the Architect agent."""

    name: str = Field(
        pattern=CHALLENGE_NAME_PATTERN,
        description="Short, hyphenated challenge name (lowercase alphanumerics + hyphens)",
    )
    category: Category
    difficulty: int = Field(ge=1, le=5, description="1 = very easy, 5 = very hard")
    vulnerability: str = Field(description="Specific flaw to exploit")
    description_hint: Optional[str] = Field(
        default=None, description="Technical summary for the Developer (not player-facing)"
    )
    language: str = Field(description="Primary language for challenge source")
    services: list[str] = Field(description="Services needed (e.g., 'web server', 'tcp socket', 'none')")
    tools_required: list[str] = Field(description="Tools a solver would need")
    flag: str = Field(description="The flag string, format CTF{...}")
    rag_references: list[str] = Field(
        default_factory=list, description="RAG challenge names used as inspiration"
    )


class ChallengeStory(BaseModel):
    """Output of the Storyteller agent."""

    title: str = Field(description="Player-facing challenge name")
    description: str = Field(description="2-4 paragraphs of flavor text for the scoreboard")
    hints: list[str] = Field(description="2-3 graduated hints (vague to specific)")
    theme: str = Field(description="One-word theme tag")


class ChallengeCode(BaseModel):
    """Output of the Developer agent."""

    files: dict[str, str] = Field(description="Mapping of filename to file content")
    entry_point: str = Field(description="Main file or command to start the challenge")
    build_notes: str = Field(default="", description="Extra build/compile steps beyond docker build")
    flag_location: str = Field(description="How and where the flag is stored")
    intended_vulnerability: str = Field(
        description="Restated vulnerability with file, function, and line range"
    )


class ChallengeInfra(BaseModel):
    """Output of the DevOps agent."""

    dockerfile: str = Field(description="Content of the Dockerfile")
    compose_file: Optional[str] = Field(
        default=None, description="Content of docker-compose.yml if multi-service"
    )
    exposed_ports: list[int] = Field(description="Ports the player connects to")
    startup_command: str = Field(description="CMD/ENTRYPOINT for the container")
    build_args: dict[str, str] = Field(
        default_factory=dict, description="Build-time arguments or env vars"
    )


class ChallengeSolver(BaseModel):
    """Output of the Solver agent."""

    solve_script: str = Field(description="Complete exploit script")
    solve_language: str = Field(default="python", description="Language of the solve script")
    dependencies: list[str] = Field(description="Pip packages needed to run the script")
    expected_flag: str = Field(description="The flag this script should extract")
    solve_steps: list[str] = Field(description="Human-readable exploit steps")


class ValidationCheck(BaseModel):
    """A single validation check result."""

    check: str
    passed: bool
    detail: str = ""


class ValidationResult(BaseModel):
    """Output of the Validator agent."""

    passed: bool
    flag_captured: bool = False
    checks: list[ValidationCheck] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    retry_instructions: str = Field(
        default="", description="Instructions for the next agent on what to fix"
    )
    retry_target: RetryTarget = Field(
        default=RetryTarget.DEVELOPER,
        description="Which agent to re-run on failure (developer or solver)",
    )


class CTFState(BaseModel):
    """Full pipeline state passed between agents.

    Each agent populates its field and passes the state forward.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_prompt: str
    model: str = Field(default="google-gla:gemini-2.5-flash", description="Built-in default model")
    use_sandbox: bool = Field(
        default=True,
        description="If True, the Validator runs the challenge in Docker. Disable for dry runs.",
    )
    event: Optional["EventConfig"] = Field(default=None, description="Loaded event config")
    _cli_model_override: Optional[str] = PrivateAttr(default=None)

    manifest: Optional[ChallengeManifest] = None
    story: Optional[ChallengeStory] = None
    code: Optional[ChallengeCode] = None
    infra: Optional[ChallengeInfra] = None
    solver: Optional[ChallengeSolver] = None
    validation: Optional[ValidationResult] = None

    retry_count: int = 0
    max_retries: int = 3

    def set_cli_model_override(self, model: str | None) -> None:
        """Called by the CLI when --model is explicitly passed. Highest precedence."""
        self._cli_model_override = model

    @property
    def has_cli_model_override(self) -> bool:
        """Whether a CLI --model override is active."""
        return self._cli_model_override is not None

    def model_for(self, agent: str) -> str:
        """Resolve which model to use for a given agent.

        Precedence (highest first):
            1. CLI --model override
            2. event.models.<agent>
            3. event.default_model
            4. self.model (built-in)
        """
        if agent not in _VALID_AGENT_NAMES:
            raise ValueError(f"Unknown agent name {agent!r}; expected one of {_VALID_AGENT_NAMES}")
        if self._cli_model_override:
            return self._cli_model_override
        if self.event is not None:
            per_agent = getattr(self.event.models, agent, None)
            if per_agent:
                return per_agent
            if self.event.default_model:
                return self.event.default_model
        return self.model
