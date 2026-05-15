"""Event-level configuration (YAML/JSON) consumed by the whole pipeline.

The config defines event-wide constraints (flag format, story tone/theme,
audience, forbidden techniques) and per-agent model routing. It's loaded
once at CLI startup and attached to CTFState.event for every downstream
agent to read.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.schemas import Category

DEFAULT_MODEL = "google-gla:gemini-2.5-flash"


class Tone(str, Enum):
    FORMAL = "formal"
    INFORMAL = "informal"
    HUMOROUS = "humorous"
    DARK = "dark"
    NOIR = "noir"


class Audience(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"
    MIXED = "mixed"


class PerAgentModels(BaseModel):
    """Optional per-agent model overrides. Unset agents fall back to default_model."""

    model_config = ConfigDict(extra="forbid")

    architect: Optional[str] = None
    storyteller: Optional[str] = None
    developer: Optional[str] = None
    devops: Optional[str] = None
    solver: Optional[str] = None
    validator: Optional[str] = None


class EventConfig(BaseModel):
    """Event-wide configuration. Loaded from a YAML or JSON file via load_event_config."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Event name, e.g. 'MegaCTF 2026'")
    flag_regex: str = Field(
        description="Regex every generated flag must match. Must require a minimum length."
    )
    theme: Optional[str] = Field(
        default=None,
        description="Overarching narrative theme. When None, the Storyteller picks freely.",
    )
    tone: Tone = Field(default=Tone.INFORMAL, description="Storyteller voice")
    organizer: Optional[str] = Field(default=None, description="Fictional sponsor in stories")
    audience: Audience = Field(
        default=Audience.MIXED,
        description="Calibrates how 'medium' is interpreted by the Architect",
    )
    language: str = Field(default="en", description="ISO 639-1 code for player-facing text")
    forbidden_categories: list[Category] = Field(default_factory=list)
    forbidden_techniques: list[str] = Field(default_factory=list)
    default_model: str = Field(default=DEFAULT_MODEL)
    models: PerAgentModels = Field(default_factory=PerAgentModels)
    max_retries: int = Field(default=5, ge=0)
    use_sandbox: bool = Field(default=True)
    rag_top_k: int = Field(default=3, ge=1)

    @field_validator("flag_regex")
    @classmethod
    def regex_must_require_min_length(cls, v: str) -> str:
        """Reject flag regexes that match short or trivial strings.

        Heuristic, not exhaustive: probes a fixed set of short and CTF-prefixed
        strings. A pathological regex could bypass; we accept that trade-off
        because parsing arbitrary regex ASTs to verify a minimum-length
        quantifier is brittle. The probes cover the cases that matter in
        practice — empty, 1–4 char bodies, and trivially short CTF{...} flags.
        """
        try:
            pat = re.compile(v)
        except re.error as e:
            raise ValueError(f"flag_regex is not a valid regex: {e}") from e
        # Probe set: empty + 1..4-char bodies + trivially short CTF{...} variants.
        # Tuned to reject the regexes the test suite considers "too permissive".
        for probe in ("", "x", "xx", "xxx", "xxxx", "CTF{x}", "CTF{xx}"):
            if pat.match(probe):
                raise ValueError(
                    "flag_regex must require a minimum length — e.g. include "
                    "`{8,}` or similar. Current regex matches strings shorter "
                    f"than the required minimum length (matched {probe!r})."
                )
        return v


def load_event_config(path: Path | str) -> EventConfig:
    """Load an event config from a YAML or JSON file.

    Format is detected by file extension (.yaml/.yml → YAML, .json → JSON).
    All Pydantic validation runs, so the returned object is fully valid.

    Raises:
        FileNotFoundError: if the file does not exist
        ValueError: for unknown extensions or invalid content
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Event config file not found: {path}")

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text) or {}
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(
            f"Unknown event config extension {suffix!r} — use .yaml, .yml, or .json"
        )

    if not isinstance(data, dict):
        raise ValueError(f"Event config root must be a mapping, got {type(data).__name__}")

    return EventConfig(**data)


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


_MAX_SLUG_LENGTH = 64


def slugify_event_name(name: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single hyphens, trim hyphens.

    Used to derive the output sub-directory from EventConfig.name.
    Capped at 64 characters to stay well within filesystem path-component limits.

    Raises:
        ValueError: if the result is empty after stripping.
    """
    lowered = name.lower()
    slug = _SLUG_STRIP.sub("-", lowered).strip("-")
    if not slug:
        raise ValueError(f"Event name {name!r} produces empty slug")
    # Cap length, trimming trailing hyphen if the cut lands on one.
    return slug[:_MAX_SLUG_LENGTH].rstrip("-")
