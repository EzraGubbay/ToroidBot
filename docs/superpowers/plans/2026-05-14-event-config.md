# Event Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an event-level config file (YAML/JSON) that drives the pipeline with required `name` and `flag_regex`, optional event metadata (tone, theme, organizer, audience, etc.), and per-agent model routing.

**Architecture:** A new `EventConfig` Pydantic model is loaded from disk and attached to `CTFState.event`. Each node reads relevant event fields when composing its prompt. The flag-regex contract is enforced both at config load (regex must reject strings shorter than 2 chars) and by the Validator (deterministic flag-match check before the LLM review). Per-agent model routing resolves via `state.model_for(agent)` with precedence: CLI `--model` > `event.models.<agent>` > `event.default_model` > built-in.

**Tech Stack:** Python 3.11+, Pydantic 2, PyYAML (new), pytest, existing Pydantic-AI pipeline.

**Spec:** [`docs/superpowers/specs/2026-05-14-event-config-design.md`](../specs/2026-05-14-event-config-design.md)

**Branch:** `feature/config` (already created)

---

## Conventions used in this plan

- `.venv/bin/python -m pytest tests/<file>.py -v` for running a single test file.
- Each task ends with a focused commit; the messages match the style of the existing repo (sentence-case, no Conventional Commits prefix).
- "Run tests, expect PASS" means *all* tests in the touched file(s), not just the new one — to catch regressions.
- Where a node currently passes `model=state.model` to `create_agent`, the plan replaces it with `model=state.model_for("<agent_name>")`. The names are: `architect`, `storyteller`, `developer`, `devops`, `solver`, `validator`.

---

## Task 1: Add PyYAML dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `pyyaml` to dependencies**

In `pyproject.toml`, change the `dependencies` list:

```toml
dependencies = [
    "pydantic>=2.0",
    "pydantic-ai>=1.96.0",
    "python-dotenv>=1.0",
    "pyyaml>=6.0",
]
```

- [ ] **Step 2: Install into venv**

Run: `uv pip install pyyaml`
Expected: installs cleanly, exit 0.

- [ ] **Step 3: Verify import**

Run: `.venv/bin/python -c "import yaml; print(yaml.__version__)"`
Expected: prints a version `>=6.0`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Add pyyaml dependency for event config loading"
```

---

## Task 2: EventConfig schema + validators

**Files:**
- Create: `agents/event_config.py`
- Test:   `tests/test_event_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_event_config.py`:

```python
"""EventConfig schema, enums, and validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.event_config import (
    Audience,
    EventConfig,
    PerAgentModels,
    Tone,
)
from agents.schemas import Category


def _base() -> dict:
    return {
        "name": "MegaCTF 2026",
        "flag_regex": r"^CTF\{[a-zA-Z0-9_-]{8,}\}$",
    }


def test_required_fields_loaded():
    cfg = EventConfig(**_base())
    assert cfg.name == "MegaCTF 2026"
    assert cfg.flag_regex == r"^CTF\{[a-zA-Z0-9_-]{8,}\}$"


def test_missing_name_raises():
    with pytest.raises(ValidationError):
        EventConfig(flag_regex=r"^CTF\{[a-z]{8,}\}$")


def test_missing_flag_regex_raises():
    with pytest.raises(ValidationError):
        EventConfig(name="x")


def test_tone_defaults_to_informal():
    cfg = EventConfig(**_base())
    assert cfg.tone == Tone.INFORMAL


def test_theme_defaults_to_none():
    cfg = EventConfig(**_base())
    assert cfg.theme is None


def test_audience_defaults_to_mixed():
    cfg = EventConfig(**_base())
    assert cfg.audience == Audience.MIXED


def test_per_agent_models_defaults_all_none():
    cfg = EventConfig(**_base())
    assert cfg.models.architect is None
    assert cfg.models.storyteller is None
    assert cfg.models.developer is None
    assert cfg.models.devops is None
    assert cfg.models.solver is None
    assert cfg.models.validator is None


def test_per_agent_models_accepts_overrides():
    cfg = EventConfig(
        **_base(),
        models=PerAgentModels(architect="openai:gpt-4.1"),
    )
    assert cfg.models.architect == "openai:gpt-4.1"


def test_per_agent_models_rejects_unknown_agent():
    with pytest.raises(ValidationError):
        EventConfig(**_base(), models={"unknown_agent": "openai:gpt-4.1"})


def test_invalid_regex_raises():
    with pytest.raises(ValidationError) as exc:
        EventConfig(name="x", flag_regex=r"[unclosed")
    assert "not a valid regex" in str(exc.value)


@pytest.mark.parametrize("regex", [
    r"^.*$",                          # matches everything (incl. empty)
    r"^CTF\{.\}$",                    # exactly one char inside
    r".",                             # matches single char
    r"",                              # empty regex matches empty
])
def test_regex_must_require_min_length(regex):
    with pytest.raises(ValidationError) as exc:
        EventConfig(name="x", flag_regex=regex)
    assert "minimum length" in str(exc.value)


def test_forbidden_categories_must_be_known():
    cfg = EventConfig(**_base(), forbidden_categories=[Category.PWN])
    assert cfg.forbidden_categories == [Category.PWN]

    with pytest.raises(ValidationError):
        EventConfig(**_base(), forbidden_categories=["nonsense"])


def test_default_model_falls_back_to_builtin():
    cfg = EventConfig(**_base())
    assert cfg.default_model == "google-gla:gemini-2.5-flash"


def test_use_sandbox_defaults_true():
    cfg = EventConfig(**_base())
    assert cfg.use_sandbox is True


def test_max_retries_defaults_three():
    cfg = EventConfig(**_base())
    assert cfg.max_retries == 3


def test_rag_top_k_defaults_three():
    cfg = EventConfig(**_base())
    assert cfg.rag_top_k == 3
```

- [ ] **Step 2: Run tests, expect failure (module doesn't exist)**

Run: `.venv/bin/python -m pytest tests/test_event_config.py -v`
Expected: ImportError for `agents.event_config`.

- [ ] **Step 3: Implement the module**

Create `agents/event_config.py`:

```python
"""Event-level configuration (YAML/JSON) consumed by the whole pipeline.

The config defines event-wide constraints (flag format, story tone/theme,
audience, forbidden techniques) and per-agent model routing. It's loaded
once at CLI startup and attached to CTFState.event for every downstream
agent to read.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

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
    max_retries: int = Field(default=3, ge=0)
    use_sandbox: bool = Field(default=True)
    rag_top_k: int = Field(default=3, ge=1)

    @field_validator("flag_regex")
    @classmethod
    def regex_must_require_min_length(cls, v: str) -> str:
        try:
            pat = re.compile(v)
        except re.error as e:
            raise ValueError(f"flag_regex is not a valid regex: {e}") from e
        if pat.match("") or pat.match("x"):
            raise ValueError(
                "flag_regex must require a minimum length — e.g. include `{8,}` "
                "or similar. Current regex matches strings shorter than 2 chars."
            )
        return v
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_event_config.py -v`
Expected: all 15 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agents/event_config.py tests/test_event_config.py
git commit -m "Add EventConfig schema with mechanical flag-regex enforcement"
```

---

## Task 3: Config file loading + slugify

**Files:**
- Modify: `agents/event_config.py`
- Modify: `tests/test_event_config.py`

- [ ] **Step 1: Append failing tests for loader + slugify**

Append to `tests/test_event_config.py`:

```python
import json
import textwrap

from agents.event_config import load_event_config, slugify_event_name


def test_load_event_config_yaml(tmp_path):
    path = tmp_path / "event.yaml"
    path.write_text(textwrap.dedent("""\
        name: MegaCTF 2026
        flag_regex: ^CTF\\{[a-zA-Z0-9_-]{8,}\\}$
        tone: noir
        theme: corporate espionage
    """), encoding="utf-8")
    cfg = load_event_config(path)
    assert cfg.name == "MegaCTF 2026"
    assert cfg.tone.value == "noir"
    assert cfg.theme == "corporate espionage"


def test_load_event_config_json(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({
        "name": "Quickstart CTF",
        "flag_regex": r"^CTF\{[a-zA-Z0-9_-]{8,}\}$",
    }), encoding="utf-8")
    cfg = load_event_config(path)
    assert cfg.name == "Quickstart CTF"
    assert cfg.tone.value == "informal"  # default


def test_load_event_config_unknown_extension_raises(tmp_path):
    path = tmp_path / "event.toml"
    path.write_text("name = 'x'\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_event_config(path)
    assert "extension" in str(exc.value).lower()


def test_load_event_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_event_config(tmp_path / "does-not-exist.yaml")


def test_load_event_config_invalid_field_raises(tmp_path):
    path = tmp_path / "event.yaml"
    path.write_text("name: x\nflag_regex: ^CTF\\{.*\\}$\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_event_config(path)


@pytest.mark.parametrize("raw,slug", [
    ("MegaCTF 2026", "megactf-2026"),
    ("Hello World", "hello-world"),
    ("  Spaced  Out  ", "spaced-out"),
    ("Mixed Case 123", "mixed-case-123"),
    ("Path/Traversal..", "path-traversal"),
    ("$(rm -rf)!!", "rm-rf"),
    ("---weird---", "weird"),
])
def test_slugify_event_name(raw, slug):
    assert slugify_event_name(raw) == slug


def test_slugify_empty_raises():
    with pytest.raises(ValueError):
        slugify_event_name("!!!")
```

- [ ] **Step 2: Run tests, expect failure (functions don't exist)**

Run: `.venv/bin/python -m pytest tests/test_event_config.py -v`
Expected: import errors for `load_event_config` and `slugify_event_name`.

- [ ] **Step 3: Implement loader + slugify**

Append to `agents/event_config.py`:

```python
import json
import re as _re
from pathlib import Path

import yaml


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


_SLUG_STRIP = _re.compile(r"[^a-z0-9]+")


def slugify_event_name(name: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single hyphens, trim hyphens.

    Used to derive the output sub-directory from EventConfig.name.

    Raises:
        ValueError: if the result is empty after stripping.
    """
    lowered = name.lower()
    slug = _SLUG_STRIP.sub("-", lowered).strip("-")
    if not slug:
        raise ValueError(f"Event name {name!r} produces empty slug")
    return slug
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_event_config.py -v`
Expected: all tests pass (originals plus 12 new).

- [ ] **Step 5: Commit**

```bash
git add agents/event_config.py tests/test_event_config.py
git commit -m "Add YAML/JSON loader and slugifier for EventConfig"
```

---

## Task 4: Extend CTFState with `event` and `model_for`

**Files:**
- Modify: `agents/schemas.py`
- Modify: `tests/test_schemas.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_schemas.py`:

```python
from agents.event_config import EventConfig, PerAgentModels
from agents.schemas import CTFState


def _state_with_event(**overrides) -> CTFState:
    cfg = EventConfig(
        name="t",
        flag_regex=r"^CTF\{[a-z0-9]{8,}\}$",
        default_model="event:default",
        models=PerAgentModels(architect="event:architect"),
        **overrides,
    )
    return CTFState(user_prompt="x", event=cfg)


def test_state_event_defaults_to_none():
    s = CTFState(user_prompt="x")
    assert s.event is None


def test_model_for_no_event_returns_builtin():
    s = CTFState(user_prompt="x")
    assert s.model_for("architect") == s.model  # "google-gla:gemini-2.5-flash"


def test_model_for_uses_event_default():
    s = _state_with_event()
    assert s.model_for("storyteller") == "event:default"


def test_model_for_per_agent_beats_default():
    s = _state_with_event()
    assert s.model_for("architect") == "event:architect"


def test_model_for_cli_override_wins():
    s = _state_with_event()
    s.set_cli_model_override("cli:override")
    assert s.model_for("architect") == "cli:override"
    assert s.model_for("storyteller") == "cli:override"


def test_model_for_unknown_agent_raises():
    s = _state_with_event()
    with pytest.raises(ValueError):
        s.model_for("unknown_role")
```

(Add `import pytest` to the top of the file if it isn't already imported.)

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/bin/python -m pytest tests/test_schemas.py -v`
Expected: failures referencing `state.event`, `model_for`, `set_cli_model_override`.

- [ ] **Step 3: Update `agents/schemas.py`**

Add the import at the top of `agents/schemas.py` (under existing imports):

```python
from agents.event_config import EventConfig
```

Note: this creates a circular import risk because `event_config.py` imports `Category` from `schemas.py`. Resolve by importing inside `TYPE_CHECKING` at module top and a string annotation for the field:

Replace the new import with:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.event_config import EventConfig
```

Then modify the `CTFState` class. Locate the existing block:

```python
class CTFState(BaseModel):
    """Full pipeline state passed between agents.

    Each agent populates its field and passes the state forward.
    """

    user_prompt: str
    model: str = Field(default="google-gla:gemini-2.5-flash", description="Model string for agents")
    use_sandbox: bool = Field(
        default=True,
        description="If True, the Validator runs the challenge in Docker. Disable for dry runs.",
    )
```

Replace with:

```python
_VALID_AGENT_NAMES = {
    "architect", "storyteller", "developer", "devops", "solver", "validator",
}


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
    _cli_model_override: Optional[str] = None
```

Also add the import for ConfigDict if not present:

```python
from pydantic import BaseModel, ConfigDict, Field
```

At the bottom of `agents/schemas.py`, add `model_for` and `set_cli_model_override` methods to `CTFState` (inside the class — append before the class ends):

```python
    def set_cli_model_override(self, model: str | None) -> None:
        """Called by the CLI when --model is explicitly passed. Highest precedence."""
        self._cli_model_override = model

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
```

Resolve the forward reference at module end:

```python
CTFState.model_rebuild()
```

(Place this after the class. The `model_rebuild()` resolves the `Optional["EventConfig"]` annotation now that the forward-declared name needs to be available.)

Actually — since `EventConfig` lives in `agents/event_config.py` which imports `Category` from this file, we have a cyclic-import problem. Resolve it at import time by importing `EventConfig` lazily *inside* `model_rebuild()` scope:

```python
def _rebuild_with_event_config():
    from agents.event_config import EventConfig  # noqa: F401
    CTFState.model_rebuild()


_rebuild_with_event_config()
```

- [ ] **Step 4: Run all tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/ -v --ignore=tests/test_sandbox_e2e.py`
Expected: every test passes (existing + new).

- [ ] **Step 5: Commit**

```bash
git add agents/schemas.py tests/test_schemas.py
git commit -m "Add event field and model_for resolver to CTFState"
```

---

## Task 5: CLI `--config` flag

**Files:**
- Modify: `orchestrator/main.py`
- Test:   `tests/test_main.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main.py`:

```python
"""CLI integration: --config loading and override precedence."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from orchestrator import main as cli


@pytest.fixture
def yaml_config(tmp_path):
    path = tmp_path / "event.yaml"
    path.write_text(
        "name: TestEvent\n"
        "flag_regex: ^CTF\\{[a-z0-9_-]{8,}\\}$\n"
        "tone: noir\n"
        "default_model: config:default\n"
        "max_retries: 5\n"
        "use_sandbox: false\n",
        encoding="utf-8",
    )
    return path


def test_build_state_no_config():
    args = cli.parse_args_from(["a prompt"])
    state = cli.build_state(args)
    assert state.event is None
    assert state.user_prompt == "a prompt"
    assert state.max_retries == 3  # built-in default
    assert state.use_sandbox is True
    assert state._cli_model_override is None


def test_build_state_with_config(yaml_config):
    args = cli.parse_args_from(["prompt", "--config", str(yaml_config)])
    state = cli.build_state(args)
    assert state.event is not None
    assert state.event.name == "TestEvent"
    # Event values flow to state where appropriate
    assert state.max_retries == 5
    assert state.use_sandbox is False
    # CLI did NOT pass --model, so no override set
    assert state._cli_model_override is None


def test_build_state_cli_model_overrides_config(yaml_config):
    args = cli.parse_args_from([
        "prompt", "--config", str(yaml_config), "--model", "cli:override",
    ])
    state = cli.build_state(args)
    assert state._cli_model_override == "cli:override"


def test_build_state_cli_no_sandbox_overrides_config(yaml_config):
    args = cli.parse_args_from([
        "prompt", "--config", str(yaml_config), "--no-sandbox",
    ])
    state = cli.build_state(args)
    # Config says use_sandbox=false; CLI --no-sandbox also says false.
    assert state.use_sandbox is False


def test_build_state_cli_max_retries_overrides_config(yaml_config):
    args = cli.parse_args_from([
        "prompt", "--config", str(yaml_config), "--max-retries", "9",
    ])
    state = cli.build_state(args)
    assert state.max_retries == 9


def test_build_state_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        args = cli.parse_args_from(["prompt", "--config", str(tmp_path / "nope.yaml")])
        cli.build_state(args)
```

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: failures for missing `parse_args_from`, `build_state`.

- [ ] **Step 3: Refactor `orchestrator/main.py` to expose pure helpers**

Replace the contents of `orchestrator/main.py` with:

```python
"""Entry point for the CTF challenge generator."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from agents.event_config import load_event_config
from agents.schemas import CTFState
from graph.pipeline import run_pipeline
from orchestrator.output import save_challenge

_DEFAULT_MAX_RETRIES = 3
_SENTINEL_MODEL = "__cli_default_model_unset__"


def parse_args_from(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a CTF challenge from a natural language prompt.",
    )
    parser.add_argument(
        "prompt",
        help='Challenge description (e.g., "Create a medium web challenge about SQL injection")',
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to event config (YAML or JSON). Optional.",
    )
    parser.add_argument(
        "--model",
        default=_SENTINEL_MODEL,
        help="Model string in <provider>:<model> format. Overrides config when set.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Validation retry attempts after the first run. Overrides config when set.",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Skip Docker sandbox in the Validator. Overrides config when set.",
    )
    return parser.parse_args(argv)


def parse_args() -> argparse.Namespace:
    return parse_args_from(sys.argv[1:])


def build_state(args: argparse.Namespace) -> CTFState:
    """Combine CLI args + optional event config into a CTFState.

    Precedence: CLI flag > event config > built-in defaults.
    """
    event = load_event_config(args.config) if args.config else None

    state = CTFState(user_prompt=args.prompt, event=event)

    # max_retries: CLI > config > built-in
    if args.max_retries is not None:
        state.max_retries = args.max_retries
    elif event is not None:
        state.max_retries = event.max_retries
    else:
        state.max_retries = _DEFAULT_MAX_RETRIES

    # use_sandbox: --no-sandbox is the only CLI override (it can only force False).
    # If --no-sandbox is set, state.use_sandbox=False regardless of config.
    if args.no_sandbox:
        state.use_sandbox = False
    elif event is not None:
        state.use_sandbox = event.use_sandbox

    # CLI --model is the global override; only set when explicitly passed.
    if args.model != _SENTINEL_MODEL:
        state.set_cli_model_override(args.model)

    return state


async def async_main() -> None:
    load_dotenv()
    args = parse_args()
    state = build_state(args)

    print(f"Generating challenge: {args.prompt}")
    if state.event:
        print(f"Event: {state.event.name}")
    print()

    state = await run_pipeline(state)

    if state.validation and state.validation.passed:
        output_dir = save_challenge(state)
        print(f"\nChallenge saved to: {output_dir}")
    else:
        print("\nChallenge generation failed after validation.", file=sys.stderr)
        if state.validation:
            for error in state.validation.errors:
                print(f"  - {error}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_main.py tests/test_schemas.py tests/test_event_config.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full unit suite (sanity check)**

Run: `.venv/bin/python -m pytest tests/ -v --ignore=tests/test_sandbox_e2e.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/main.py tests/test_main.py
git commit -m "Add --config CLI flag with event-config + override precedence"
```

---

## Task 6: All nodes use `state.model_for(agent_name)`

**Files:**
- Modify: `graph/nodes/architect_node.py`
- Modify: `graph/nodes/storyteller_node.py`
- Modify: `graph/nodes/developer_node.py`
- Modify: `graph/nodes/devops_node.py`
- Modify: `graph/nodes/solver_node.py`
- Modify: `graph/nodes/validator_node.py`

Mechanical refactor. No new tests — existing tests must continue to pass.

- [ ] **Step 1: Update each node's `create_agent` call**

In each node, find the existing `create_agent(...)` call and change `model=state.model` to `model=state.model_for("<agent>")`:

- `architect_node.py`: `model=state.model_for("architect")`
- `storyteller_node.py`: `model=state.model_for("storyteller")`
- `developer_node.py`: `model=state.model_for("developer")`
- `devops_node.py`: `model=state.model_for("devops")`
- `solver_node.py`: `model=state.model_for("solver")`
- `validator_node.py`: in `_llm_review`, change to `model=state.model_for("validator")`

Concrete diff per file — open each and apply:

`graph/nodes/architect_node.py` change:
```python
agent = create_agent("rag_architect", ChallengeManifest, model=state.model)
```
to:
```python
agent = create_agent("rag_architect", ChallengeManifest, model=state.model_for("architect"))
```

Apply the analogous change in all six nodes.

- [ ] **Step 2: Run the suite, expect PASS**

Run: `.venv/bin/python -m pytest tests/ -v --ignore=tests/test_sandbox_e2e.py`
Expected: all tests still pass — this was a mechanical refactor.

- [ ] **Step 3: Commit**

```bash
git add graph/nodes/*.py
git commit -m "Route every node through state.model_for for per-agent models"
```

---

## Task 7: Architect injects event constraints into its prompt

**Files:**
- Modify: `graph/nodes/architect_node.py`
- Modify: `tests/test_architect_node.py` (new)

The Architect's prompt becomes a pure function `_build_architect_prompt(state, rag_context) -> str` so it can be tested without invoking the LLM.

- [ ] **Step 1: Write failing tests**

Create `tests/test_architect_node.py`:

```python
"""Architect prompt composition with event constraints."""

from __future__ import annotations

import pytest

from agents.event_config import Audience, EventConfig
from agents.schemas import Category, CTFState
from graph.nodes.architect_node import _build_architect_prompt


def _state(event=None) -> CTFState:
    return CTFState(user_prompt="medium web SQLi", event=event)


def test_prompt_no_event_omits_event_block():
    prompt = _build_architect_prompt(_state(), rag_context="RAG_BODY")
    assert "EVENT CONSTRAINTS" not in prompt
    assert "medium web SQLi" in prompt
    assert "RAG_BODY" in prompt


def test_prompt_includes_flag_regex_when_event_set():
    cfg = EventConfig(name="t", flag_regex=r"^MEGA\{[a-z]{8,}\}$")
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "EVENT CONSTRAINTS" in prompt
    assert r"^MEGA\{[a-z]{8,}\}$" in prompt


def test_prompt_includes_audience():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        audience=Audience.BEGINNER,
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Audience: beginner" in prompt


def test_prompt_includes_forbidden_categories():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        forbidden_categories=[Category.PWN, Category.REV],
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Forbidden categories" in prompt
    assert "pwn" in prompt and "rev" in prompt


def test_prompt_includes_forbidden_techniques():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        forbidden_techniques=["race conditions", "time-based oracles"],
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Forbidden techniques" in prompt
    assert "race conditions" in prompt


def test_prompt_includes_theme_when_set():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        theme="corporate espionage",
    )
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Theme: corporate espionage" in prompt


def test_prompt_omits_theme_when_unset():
    cfg = EventConfig(name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    prompt = _build_architect_prompt(_state(cfg), rag_context="X")
    assert "Theme:" not in prompt
```

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/bin/python -m pytest tests/test_architect_node.py -v`
Expected: ImportError for `_build_architect_prompt`.

- [ ] **Step 3: Refactor architect_node**

Replace `graph/nodes/architect_node.py` with:

```python
"""Architect node — designs the challenge concept using RAG context."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeManifest, CTFState
from orchestrator.rag import retrieve_similar_challenges


def _build_architect_prompt(state: CTFState, rag_context: str) -> str:
    parts = [
        f"User request: {state.user_prompt}",
        f"Similar challenges from knowledge base:\n{rag_context}",
    ]
    if state.event is not None:
        ev = state.event
        ev_lines = [
            "## EVENT CONSTRAINTS (hard requirements)",
            f"Flag must match this regex: {ev.flag_regex}",
            f"Audience: {ev.audience.value}",
        ]
        if ev.theme:
            ev_lines.append(f"Theme: {ev.theme}")
        if ev.forbidden_categories:
            cats = ", ".join(c.value for c in ev.forbidden_categories)
            ev_lines.append(f"Forbidden categories (do not pick): {cats}")
        if ev.forbidden_techniques:
            techs = ", ".join(ev.forbidden_techniques)
            ev_lines.append(f"Forbidden techniques: {techs}")
        parts.append("\n".join(ev_lines))
    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the Architect agent to produce a ChallengeManifest."""
    top_k = state.event.rag_top_k if state.event else 3
    rag_context = retrieve_similar_challenges(state.user_prompt, top_k=top_k)

    agent = create_agent("rag_architect", ChallengeManifest, model=state.model_for("architect"))

    prompt = _build_architect_prompt(state, rag_context)
    result = await agent.run(prompt)

    state.manifest = result.output
    return state
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_architect_node.py tests/test_schemas.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/architect_node.py tests/test_architect_node.py
git commit -m "Architect: inject event constraints into prompt"
```

---

## Task 8: Storyteller injects tone, theme, organizer, language

**Files:**
- Modify: `graph/nodes/storyteller_node.py`
- Modify: `tests/test_storyteller_node.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_storyteller_node.py`:

```python
"""Storyteller prompt composition with event tone/theme/organizer."""

from __future__ import annotations

from agents.event_config import EventConfig, Tone
from agents.schemas import Category, ChallengeManifest, CTFState
from graph.nodes.storyteller_node import _build_storyteller_prompt


def _manifest() -> ChallengeManifest:
    return ChallengeManifest(
        name="test-1",
        category=Category.WEB,
        difficulty=2,
        vulnerability="SQLi",
        language="python",
        services=["web server"],
        tools_required=["requests"],
        flag="CTF{abcdefgh}",
    )


def _state(event=None) -> CTFState:
    return CTFState(user_prompt="x", manifest=_manifest(), event=event)


def test_prompt_no_event_omits_event_block():
    prompt = _build_storyteller_prompt(_state())
    assert "EVENT STYLE" not in prompt


def test_prompt_includes_tone():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$", tone=Tone.NOIR,
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Tone: noir" in prompt


def test_prompt_includes_theme_when_set():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        theme="space heist",
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Theme: space heist" in prompt


def test_prompt_includes_organizer_when_set():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        organizer="Aperture Sec Labs",
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Aperture Sec Labs" in prompt


def test_prompt_includes_language_when_non_english():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        language="he",
    )
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Language: he" in prompt


def test_prompt_omits_language_block_when_english():
    """English is the default — don't add noise to the prompt."""
    cfg = EventConfig(name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    prompt = _build_storyteller_prompt(_state(cfg))
    assert "Language:" not in prompt
```

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/bin/python -m pytest tests/test_storyteller_node.py -v`
Expected: ImportError for `_build_storyteller_prompt`.

- [ ] **Step 3: Refactor storyteller_node**

Replace `graph/nodes/storyteller_node.py` with:

```python
"""Storyteller node — creates the narrative wrapper for the challenge."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeStory, CTFState


def _build_storyteller_prompt(state: CTFState) -> str:
    if state.manifest is None:
        raise RuntimeError("Architect must run before Storyteller")

    parts = [f"Create a story for this challenge:\n{state.manifest.model_dump_json(indent=2)}"]
    if state.event is not None:
        ev = state.event
        ev_lines = [
            "## EVENT STYLE (overrides default tone-by-difficulty guidance)",
            f"Tone: {ev.tone.value}",
        ]
        if ev.theme:
            ev_lines.append(f"Theme: {ev.theme}")
        if ev.organizer:
            ev_lines.append(f"Fictional organizer to weave into the story: {ev.organizer}")
        if ev.language != "en":
            ev_lines.append(f"Language: {ev.language} — write player-facing text in this language.")
        parts.append("\n".join(ev_lines))
    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the Storyteller agent to produce a ChallengeStory."""
    if state.manifest is None:
        raise RuntimeError("Architect must run before Storyteller")

    agent = create_agent("storyteller", ChallengeStory, model=state.model_for("storyteller"))

    prompt = _build_storyteller_prompt(state)
    result = await agent.run(prompt)

    state.story = result.output
    return state
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_storyteller_node.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/storyteller_node.py tests/test_storyteller_node.py
git commit -m "Storyteller: inject event tone, theme, organizer, language"
```

---

## Task 9: Developer injects forbidden_techniques + uses event rag_top_k

**Files:**
- Modify: `graph/nodes/developer_node.py`
- Modify: `tests/test_developer_node.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_developer_node.py`:

```python
"""Developer prompt composition with event forbidden_techniques."""

from __future__ import annotations

from agents.event_config import EventConfig
from agents.schemas import (
    Category,
    ChallengeManifest,
    ChallengeStory,
    CTFState,
)
from graph.nodes.developer_node import _build_developer_prompt


def _state(event=None, validation=None) -> CTFState:
    return CTFState(
        user_prompt="x",
        manifest=ChallengeManifest(
            name="t",
            category=Category.WEB,
            difficulty=2,
            vulnerability="SQLi",
            language="python",
            services=["web server"],
            tools_required=["requests"],
            flag="CTF{abcdefgh}",
        ),
        story=ChallengeStory(
            title="t", description="d", hints=["a", "b"], theme="x",
        ),
        event=event,
        validation=validation,
    )


def test_prompt_no_event_omits_event_block():
    prompt = _build_developer_prompt(_state(), rag_context="RAG")
    assert "EVENT CONSTRAINTS" not in prompt
    assert "RAG" in prompt


def test_prompt_includes_forbidden_techniques():
    cfg = EventConfig(
        name="t", flag_regex=r"^CTF\{[a-z]{8,}\}$",
        forbidden_techniques=["race conditions", "TOCTOU"],
    )
    prompt = _build_developer_prompt(_state(cfg), rag_context="R")
    assert "EVENT CONSTRAINTS" in prompt
    assert "race conditions" in prompt
    assert "TOCTOU" in prompt
```

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/bin/python -m pytest tests/test_developer_node.py -v`
Expected: ImportError.

- [ ] **Step 3: Refactor developer_node**

Replace `graph/nodes/developer_node.py` with:

```python
"""Developer node — writes the vulnerable source code."""

from __future__ import annotations

from agents.factory import create_agent
from agents.schemas import ChallengeCode, CTFState
from orchestrator.rag import retrieve_similar_challenges


def _build_developer_prompt(state: CTFState, rag_context: str) -> str:
    if state.manifest is None:
        raise RuntimeError("Architect must run before Developer")
    if state.story is None:
        raise RuntimeError("Storyteller must run before Developer")

    parts = [
        f"Challenge manifest:\n{state.manifest.model_dump_json(indent=2)}",
        f"Challenge story:\n{state.story.model_dump_json(indent=2)}",
        f"Similar challenges from knowledge base (study these for implementation patterns):\n{rag_context}",
    ]

    if state.event is not None and state.event.forbidden_techniques:
        techs = ", ".join(state.event.forbidden_techniques)
        parts.append(
            "## EVENT CONSTRAINTS\n"
            f"Forbidden techniques (do not use): {techs}"
        )

    if state.validation and state.validation.retry_instructions:
        parts.append(
            f"PREVIOUS ATTEMPT FAILED. Fix these issues:\n{state.validation.retry_instructions}"
        )

    return "\n\n".join(parts)


async def run(state: CTFState) -> CTFState:
    """Run the Developer agent to produce ChallengeCode."""
    if state.manifest is None:
        raise RuntimeError("Architect must run before Developer")
    if state.story is None:
        raise RuntimeError("Storyteller must run before Developer")

    top_k = state.event.rag_top_k if state.event else 3
    rag_context = retrieve_similar_challenges(
        f"{state.manifest.category} {state.manifest.vulnerability} {state.manifest.language}",
        top_k=top_k,
    )

    agent = create_agent("ctf_developer", ChallengeCode, model=state.model_for("developer"))

    prompt = _build_developer_prompt(state, rag_context)
    result = await agent.run(prompt)

    state.code = result.output
    return state
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_developer_node.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/developer_node.py tests/test_developer_node.py
git commit -m "Developer: inject event forbidden_techniques and use event rag_top_k"
```

---

## Task 10: Solver uses event rag_top_k

**Files:**
- Modify: `graph/nodes/solver_node.py`

No new tests — the only changes are `model_for` (Task 6) and `rag_top_k` (a parameter to an already-tested function).

- [ ] **Step 1: Update solver_node**

Replace the RAG call in `graph/nodes/solver_node.py` from:

```python
rag_context = retrieve_similar_challenges(
    f"{state.manifest.category} {state.manifest.vulnerability} exploit solve"
)
```

to:

```python
top_k = state.event.rag_top_k if state.event else 3
rag_context = retrieve_similar_challenges(
    f"{state.manifest.category} {state.manifest.vulnerability} exploit solve",
    top_k=top_k,
)
```

- [ ] **Step 2: Run full suite, expect PASS**

Run: `.venv/bin/python -m pytest tests/ -v --ignore=tests/test_sandbox_e2e.py`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add graph/nodes/solver_node.py
git commit -m "Solver: use event rag_top_k for RAG retrieval"
```

---

## Task 11: Validator: deterministic `flag_matches_regex` check

**Files:**
- Modify: `graph/nodes/validator_node.py`
- Modify: `tests/test_validator_node.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_validator_node.py`:

```python
from agents.event_config import EventConfig
from graph.nodes.validator_node import _flag_matches_regex_check


def _state_with_event_and_flag(state, flag, regex):
    state.manifest.__dict__["flag"] = flag
    state.event = EventConfig(name="t", flag_regex=regex)
    return state


def test_flag_matches_regex_skipped_when_no_event(state):
    state.event = None
    check = _flag_matches_regex_check(state)
    assert check is None


def test_flag_matches_regex_passes_when_match(state):
    _state_with_event_and_flag(state, "CTF{abcdefgh}", r"^CTF\{[a-z]{8,}\}$")
    check = _flag_matches_regex_check(state)
    assert check is not None
    assert check.passed
    assert check.check == "flag_matches_regex"


def test_flag_matches_regex_fails_when_no_match(state):
    _state_with_event_and_flag(state, "WRONG{abcdefgh}", r"^CTF\{[a-z]{8,}\}$")
    check = _flag_matches_regex_check(state)
    assert check is not None
    assert not check.passed
    assert r"^CTF\{[a-z]{8,}\}$" in check.detail
```

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/bin/python -m pytest tests/test_validator_node.py -v`
Expected: ImportError for `_flag_matches_regex_check`.

- [ ] **Step 3: Add the check to validator_node**

In `graph/nodes/validator_node.py`, add:

```python
import re
```
near the top with other imports, then add this helper near the other check helpers:

```python
def _flag_matches_regex_check(state: CTFState) -> ValidationCheck | None:
    """Deterministic: if event.flag_regex is set, the generated flag must match it."""
    if state.event is None:
        return None
    assert state.manifest is not None  # narrowed by caller
    flag = state.manifest.flag
    regex = state.event.flag_regex
    if re.match(regex, flag):
        return ValidationCheck(check="flag_matches_regex", passed=True, detail="ok")
    return ValidationCheck(
        check="flag_matches_regex", passed=False,
        detail=f"flag {flag!r} does not match event regex {regex}",
    )
```

In the `run` function, integrate this check into the checks list — locate the existing line:

```python
checks: list[ValidationCheck] = [_flag_in_source_check(state)]
```

and replace with:

```python
checks: list[ValidationCheck] = [_flag_in_source_check(state)]
regex_check = _flag_matches_regex_check(state)
if regex_check is not None:
    checks.append(regex_check)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_validator_node.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add graph/nodes/validator_node.py tests/test_validator_node.py
git commit -m "Validator: add deterministic flag_matches_regex check when event is set"
```

---

## Task 12: Output directory respects event slug

**Files:**
- Modify: `orchestrator/output.py`
- Modify: `tests/test_output.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_output.py`:

```python
from agents.event_config import EventConfig


def test_save_challenge_uses_event_slug_when_event_set(tmp_path, monkeypatch, state):
    monkeypatch.setattr(output, "OUTPUT_DIR", tmp_path)
    state.event = EventConfig(name="MegaCTF 2026", flag_regex=r"^CTF\{[a-z]{8,}\}$")
    out_dir = output.save_challenge(state)
    assert out_dir == tmp_path / "megactf-2026" / "sample-web-1"
    assert (out_dir / "Dockerfile").exists()


def test_save_challenge_no_event_keeps_flat_layout(tmp_path, monkeypatch, state):
    monkeypatch.setattr(output, "OUTPUT_DIR", tmp_path)
    out_dir = output.save_challenge(state)
    assert out_dir == tmp_path / "sample-web-1"
```

- [ ] **Step 2: Run tests, expect failure**

Run: `.venv/bin/python -m pytest tests/test_output.py -v`
Expected: the new test for the event slug fails.

- [ ] **Step 3: Update output.py**

In `orchestrator/output.py`, locate:

```python
challenge_dir = OUTPUT_DIR / state.manifest.name
```

Replace with:

```python
if state.event is not None:
    from agents.event_config import slugify_event_name
    base = OUTPUT_DIR / slugify_event_name(state.event.name)
else:
    base = OUTPUT_DIR
challenge_dir = base / state.manifest.name
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `.venv/bin/python -m pytest tests/test_output.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/output.py tests/test_output.py
git commit -m "Output: nest challenge dir under event slug when event is set"
```

---

## Task 13: Sample configs

**Files:**
- Create: `examples/configs/megactf-2026.yaml`
- Create: `examples/configs/minimal.json`

No tests — these are static fixtures. Their loadability is implicitly covered by `test_load_event_config_yaml` and `_json` in Task 3.

- [ ] **Step 1: Create the full-fat YAML example**

Create `examples/configs/megactf-2026.yaml` with exactly:

```yaml
name: MegaCTF 2026
flag_regex: ^MEGACTF\{[a-zA-Z0-9_-]{8,}\}$
theme: corporate espionage in 2099
tone: noir
organizer: Aperture Sec Labs
audience: intermediate
language: en
forbidden_categories: []
forbidden_techniques:
  - time-based oracles
  - race conditions
default_model: google-gla:gemini-2.5-flash
models:
  architect: openai:gpt-4.1
  solver: anthropic:claude-sonnet-4-5
max_retries: 3
use_sandbox: true
rag_top_k: 3
```

- [ ] **Step 2: Create the minimal JSON example**

Create `examples/configs/minimal.json` with exactly:

```json
{
  "name": "Quickstart CTF",
  "flag_regex": "^CTF\\{[a-zA-Z0-9_-]{8,}\\}$"
}
```

- [ ] **Step 3: Verify both load without error**

Run:
```bash
.venv/bin/python -c "
from agents.event_config import load_event_config
print(load_event_config('examples/configs/megactf-2026.yaml').name)
print(load_event_config('examples/configs/minimal.json').name)
"
```

Expected output:
```
MegaCTF 2026
Quickstart CTF
```

- [ ] **Step 4: Commit**

```bash
git add examples/configs/megactf-2026.yaml examples/configs/minimal.json
git commit -m "Add sample event configs (YAML full-fat + JSON minimal)"
```

---

## Task 14: Update DEV.md

**Files:**
- Modify: `DEV.md`

- [ ] **Step 1: Replace the "Input modes" section**

In `DEV.md`, find the section starting with `**Input modes:**` and replace it with:

```markdown
**Input modes:**
1. Prompt-only: `ctf-poc "<prompt>"` — same as before.
2. Prompt + event config: `ctf-poc "<prompt>" --config examples/configs/megactf-2026.yaml`. The event config (YAML or JSON) defines event-wide constraints — flag regex, tone, theme, audience, organizer, forbidden techniques — plus per-agent model routing. CLI flags (`--model`, `--max-retries`, `--no-sandbox`) override config values. See [`examples/configs/`](examples/configs/) for samples and [`docs/superpowers/specs/2026-05-14-event-config-design.md`](docs/superpowers/specs/2026-05-14-event-config-design.md) for the full schema.
3. CVE-based: prompt of the form `Build a challenge inspired by CVE-YYYY-NNNNN`. Reuses the same pipeline; the Architect retrieves CVE-adjacent context from the RAG corpus.
```

- [ ] **Step 2: Commit**

```bash
git add DEV.md
git commit -m "Document --config event file in DEV.md"
```

---

## Task 15: Final integration sweep

**Files:** none new

- [ ] **Step 1: Run the full unit suite**

Run: `.venv/bin/python -m pytest tests/ -v --ignore=tests/test_sandbox_e2e.py`
Expected: every test passes. Count should be roughly the original 42 plus ~50 new tests across `test_event_config.py`, `test_main.py`, `test_architect_node.py`, `test_storyteller_node.py`, `test_developer_node.py`, and the extensions to `test_validator_node.py` and `test_schemas.py` and `test_output.py`.

- [ ] **Step 2: Run the Docker E2E test (if Docker is up)**

Run: `.venv/bin/python -m pytest tests/test_sandbox_e2e.py -v`
Expected: PASS (or SKIP if Docker isn't running).

- [ ] **Step 3: Smoke test the CLI**

Run (no API key needed — only argument parsing):
```bash
.venv/bin/python -c "
from orchestrator.main import parse_args_from, build_state
args = parse_args_from(['demo prompt', '--config', 'examples/configs/megactf-2026.yaml'])
state = build_state(args)
print('event name:', state.event.name)
print('flag regex:', state.event.flag_regex)
print('architect model:', state.model_for('architect'))
print('storyteller model:', state.model_for('storyteller'))
print('max_retries:', state.max_retries)
"
```

Expected output:
```
event name: MegaCTF 2026
flag regex: ^MEGACTF\{[a-zA-Z0-9_-]{8,}\}$
architect model: openai:gpt-4.1
storyteller model: google-gla:gemini-2.5-flash
max_retries: 3
```

- [ ] **Step 4: Push the branch**

```bash
git push -u origin feature/config
```

- [ ] **Step 5: Open the PR**

Run:
```bash
gh pr create --title "Add event config file for tone, theme, flag regex, per-agent models" --body "$(cat <<'EOF'
## Summary
- Adds `EventConfig` Pydantic model loaded from YAML or JSON via `--config <path>`
- Required: `name`, `flag_regex` (mechanically enforced to require min length)
- Optional: `tone` (default `informal`), `theme`, `organizer`, `audience`, `language`, `forbidden_categories`, `forbidden_techniques`, `default_model`, `models` (per-agent), `max_retries`, `use_sandbox`, `rag_top_k`
- CLI flags (`--model`, `--max-retries`, `--no-sandbox`) override config; config overrides built-in defaults
- Validator gets a new deterministic `flag_matches_regex` check
- Output dir nests under event slug when set: `output/<event-slug>/<challenge>/`
- ~50 new unit tests; existing 43 still pass

Spec: `docs/superpowers/specs/2026-05-14-event-config-design.md`

## Test plan
- [ ] `pytest tests/ --ignore=tests/test_sandbox_e2e.py` — all pass
- [ ] Docker E2E (`pytest tests/test_sandbox_e2e.py`) still passes
- [ ] Run an end-to-end LLM pipeline with `examples/configs/megactf-2026.yaml` once a key is available

EOF
)"
```

---

## Self-review (already done — keeping notes for traceability)

- **Spec coverage:** Every required and optional schema field has a task that consumes it. Required: `name` (T2 schema, T12 output dir), `flag_regex` (T2 validator, T7 architect prompt, T11 validator check). Optional: `tone` (T8), `theme` (T7, T8), `organizer` (T8), `audience` (T7), `language` (T8), `forbidden_categories` (T7), `forbidden_techniques` (T7, T9), `default_model` + `models` (T4 model_for, T6 node adoption), `max_retries` (T5), `use_sandbox` (T5), `rag_top_k` (T7, T9, T10). CLI override precedence covered in T5.
- **No placeholders:** every step contains complete code or exact commands. No "TBD", no "similar to above", no "handle errors appropriately".
- **Type consistency:** `model_for`, `_cli_model_override`, `set_cli_model_override`, `_build_<role>_prompt`, `_flag_matches_regex_check`, `load_event_config`, `slugify_event_name` are used consistently across all tasks that reference them.
