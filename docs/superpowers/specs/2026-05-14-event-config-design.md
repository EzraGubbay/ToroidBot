# Event Config File (YAML/JSON) — Design Spec

**Date:** 2026-05-14
**Status:** Approved for implementation
**Author:** noahkmoore + Claude

## Summary

Add a top-level config file (`event.yaml` or `event.json`) that defines event-wide constraints — name, flag format, story tone/theme, organizer, audience, forbidden techniques, and per-agent model routing — and is consumed by the existing pipeline as an `EventConfig` Pydantic model carried on `CTFState`. The positional CLI prompt continues to drive the specific challenge being generated; the config supplies context every agent honors.

## Goals

- One config drives a whole CTF event: every challenge generated under it follows the same flag format, tone, theme, and constraints.
- Mechanical (not convention-based) enforcement of the flag-regex contract — wrong configs fail fast, before any LLM call.
- Per-agent model routing so harder reasoning (Architect, Solver) can be routed to stronger models while cheaper ones run Storyteller/DevOps.
- Backward-compatible: omitting `--config` preserves today's behavior exactly.

## Non-goals (v1)

- Per-agent model override via CLI flag (only `--model` global override is supported; per-agent routing is config-only).
- `port_range`, `base_image_allowlist`, `max_image_size_mb` — infra tuning, no current need.
- Batch / multi-challenge mode (one challenge per invocation).
- Languages other than English for player-facing text (the `language` field exists for forward compatibility but the RAG corpus is English-only).

## Schema

### Required fields

| Field | Type | Example | Notes |
|---|---|---|---|
| `name` | `str` | `MegaCTF 2026` | Event name. Used in stories. Slugified (lowercase, spaces and unsafe chars → `-`, collapse repeats) for the output sub-directory: `output/<event-slug>/<challenge>/`. Slugifier lives next to `load_event_config` in `agents/event_config.py`. |
| `flag_regex` | `str` (regex) | `^MEGACTF\{[a-zA-Z0-9_-]{8,}\}$` | Per-event flag format. Enforced mechanically — see [Regex enforcement](#regex-enforcement). |

### Optional fields

| Field | Type | Default | Consumer |
|---|---|---|---|
| `theme` | `str \| None` | `None` → Storyteller picks freely | Storyteller |
| `tone` | enum: `formal \| informal \| humorous \| dark \| noir` | `informal` | Storyteller |
| `organizer` | `str \| None` | `None` | Storyteller (story flavor) |
| `audience` | enum: `beginner \| intermediate \| expert \| mixed` | `mixed` | Architect (difficulty calibration) |
| `language` | `str` (ISO 639-1) | `"en"` | Storyteller (player-facing text) |
| `forbidden_categories` | `list[Category]` (imported from `agents.schemas`) | `[]` | Architect |
| `forbidden_techniques` | `list[str]` | `[]` | Architect + Developer |
| `default_model` | `str` (`<provider>:<model>`) | `"google-gla:gemini-2.5-flash"` | All agents (fallback) |
| `models` | `PerAgentModels` | empty (all `None`) | Per-agent override |
| `max_retries` | `int` | `3` | Pipeline retry budget |
| `use_sandbox` | `bool` | `true` | Validator |
| `rag_top_k` | `int` | `3` | RAG retriever |

### PerAgentModels sub-model

```yaml
models:
  architect:  openai:gpt-4.1            # optional
  storyteller: google-gla:gemini-2.5-flash
  developer:  anthropic:claude-sonnet-4-5
  devops:     null   # falls back to default_model
  solver:     anthropic:claude-sonnet-4-5
  validator:  null
```

All keys are optional. Unset agents fall back through the [resolution precedence](#per-agent-model-routing).

## Behavior

### Loading

`--config <path>` is an optional CLI flag. When set:

1. Read the file. Format detected by extension: `.yaml` / `.yml` → YAML; `.json` → JSON. Any other extension is a hard error.
2. Parse into `EventConfig` via Pydantic. Validation errors surface as a single `ValueError` listing every problem.
3. Attach to `CTFState.event`.

Without `--config`, `CTFState.event` is `None` and the pipeline behaves identically to today.

### Override precedence

For values that can be set in multiple places, highest precedence wins:

1. **CLI flag** (`--model`, `--max-retries`, `--no-sandbox`)
2. **Per-agent config field** (`models.<agent>`) — applies only to `model_for()`
3. **Config event-level field** (`default_model`, `max_retries`, `use_sandbox`)
4. **Built-in default** on `CTFState`

This means a user can scaffold an event config and still tweak one knob from the CLI for a single run.

### Failure modes

- Missing required field → fail before any LLM call, exit code 1.
- Invalid regex (won't compile, or matches strings shorter than 2 chars) → same.
- Unknown YAML/JSON extension → same.
- Unknown agent name under `models` → same (Pydantic schema rejects).

## Codebase changes

### New files

| File | Purpose |
|---|---|
| `agents/event_config.py` | `EventConfig` + `PerAgentModels` Pydantic models, validators, `load_event_config(path) -> EventConfig`, `Tone`/`Audience` enums |
| `examples/configs/megactf-2026.yaml` | Full-fat example showing every field |
| `examples/configs/minimal.json` | Bare-minimum example (just required fields) |
| `tests/test_event_config.py` | Unit tests for schema validation, regex enforcement, file loading, model_for precedence |

### Modified files

| File | Change |
|---|---|
| `agents/schemas.py` | Add `event: Optional[EventConfig] = None` to `CTFState`. Add `_model_override: Optional[str] = None` (private — set only by CLI). Add `model_for(agent: str) -> str` helper. The existing `model: str` field stays as the built-in default (precedence rung 4); CLI no longer writes to it directly. |
| `orchestrator/main.py` | Add `--config <path>` CLI flag. Load event config, attach to state. Apply CLI flag overrides after load. |
| `graph/nodes/architect_node.py` | When `state.event` is set: prepend event constraints to the prompt (flag_regex, audience, forbidden_categories, forbidden_techniques, theme if set). Use `state.model_for("architect")`. |
| `graph/nodes/storyteller_node.py` | Inject tone, theme, organizer, language. Use `state.model_for("storyteller")`. |
| `graph/nodes/developer_node.py` | Inject forbidden_techniques. Use `state.model_for("developer")`. |
| `graph/nodes/devops_node.py` | Use `state.model_for("devops")`. |
| `graph/nodes/solver_node.py` | Use `state.model_for("solver")`. |
| `graph/nodes/validator_node.py` | Add deterministic `flag_matches_regex` check. Use `state.model_for("validator")` for the LLM review. |
| `orchestrator/rag.py` | `retrieve_similar_challenges(query, top_k=...)` already supports `top_k`; nodes pass `state.event.rag_top_k` when event is set. |
| `orchestrator/output.py` | If `state.event` is set, write to `output/<event-slug>/<challenge>/` instead of `output/<challenge>/`. |
| `pyproject.toml` | Add `pyyaml >= 6.0` to dependencies. |
| `README.md` | Document `--config`, link to `examples/configs/`. |
| `DEV.md` | Update input modes section. |

## How config reaches each agent

Each field has exactly one prompt site — no duplication. The Architect, for example, sees `flag_regex` (because it generates the flag) but not `tone` (the Storyteller's concern).

| Field | Agent | Mechanism |
|---|---|---|
| `flag_regex` | Architect | Prompt directive: *"The flag must match this regex: `<regex>`"* |
| `flag_regex` | Validator | Deterministic check: `re.match(regex, state.manifest.flag)` |
| `theme` | Storyteller | Prompt: *"This challenge is part of an event themed: `<theme>`. The story must fit this universe."* — only when set |
| `tone` | Storyteller | Replaces the difficulty-derived tone rule in `skills/storyteller.md`. The skill is edited to defer to event.tone when set. |
| `organizer` | Storyteller | Prompt flavor: *"Fictional organizer: `<organizer>`. May appear in the story."* |
| `audience` | Architect | Difficulty calibration: *"Audience is `<audience>`. A 'medium' challenge for beginners is simpler than for experts."* |
| `language` | Storyteller | Prompt: *"Player-facing text must be in `<language>`."* |
| `forbidden_categories` | Architect | Hard constraint: *"You may NOT pick these categories: `<list>`."* |
| `forbidden_techniques` | Architect + Developer | Both prompts get the list as a hard avoid-rule. |
| `rag_top_k` | RAG calls | `retrieve_similar_challenges(query, top_k=state.event.rag_top_k)` in Architect, Developer, Solver nodes |
| `default_model`, `models.*` | All nodes | Via `state.model_for(agent_name)` |
| `max_retries` | Pipeline | `state.max_retries = event.max_retries` after loading |
| `use_sandbox` | Validator | `state.use_sandbox = event.use_sandbox` after loading |

## Regex enforcement

In `EventConfig`:

```python
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

This catches both `^CTF\{.*\}$` (matches anything) and `^CTF\{.\}$` (one char). The convention message in the error tells the user how to fix it.

The Architect is also told the regex in its prompt, so the LLM has the constraint at generation time. The Validator's deterministic check is the belt-and-braces guarantee.

## Per-agent model routing

Helper on `CTFState`:

```python
def model_for(self, agent: str) -> str:
    """Resolve which model to use for a given agent.

    Precedence (highest first):
      1. CLI `--model` flag (sets self._model_override)
      2. event.models.<agent>
      3. event.default_model
      4. self.model (built-in default)
    """
    if self._model_override:
        return self._model_override
    if self.event:
        per_agent = getattr(self.event.models, agent, None)
        if per_agent:
            return per_agent
        if self.event.default_model:
            return self.event.default_model
    return self.model
```

`_model_override` is set by the CLI when `--model` is explicitly passed (detected via argparse default sentinel). Without the override, the helper falls through to config.

Every node is updated from `create_agent(skill, output, model=state.model)` to `create_agent(skill, output, model=state.model_for("<agent_name>"))`.

## CLI behavior

```bash
# No config (today's behavior, unchanged)
ctf-poc "Create a medium web challenge about SQL injection"

# Config-driven
ctf-poc "Create a medium web challenge about SQL injection" --config examples/configs/megactf-2026.yaml

# CLI flag overrides config
ctf-poc "..." --config event.yaml --model openai:gpt-4.1   # global override beats config.models
ctf-poc "..." --config event.yaml --no-sandbox              # overrides config.use_sandbox
```

The positional prompt remains required. `--config` is optional.

## Sample configs (shipped in repo)

**`examples/configs/megactf-2026.yaml`** — full-fat:

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

**`examples/configs/minimal.json`** — required only:

```json
{
  "name": "Quickstart CTF",
  "flag_regex": "^CTF\\{[a-zA-Z0-9_-]{8,}\\}$"
}
```

## Testing

| Test file | Coverage |
|---|---|
| `tests/test_event_config.py` (new) | `EventConfig` schema: required fields enforced; tone defaults to `informal`; theme accepts `None`; regex validator rejects `^.*$`, `^.\{1,\}$`, invalid syntax; enum values accepted/rejected; PerAgentModels unknown keys rejected. `load_event_config`: YAML round-trip, JSON round-trip, unknown extension errors. `state.model_for()`: full precedence matrix — CLI override > per-agent > default_model > built-in. |
| `tests/test_validator_node.py` (extend) | New deterministic `flag_matches_regex` check passes when flag matches, fails with regex in error detail when it doesn't. |
| `tests/test_main.py` (new) | `--config` integration: loading wires `state.event`; CLI `--model` sets `_model_override`; missing config file is a clear error. |

E2E sandbox test stays as-is — config layer is additive.

## Dependencies

- `pyyaml >= 6.0` added to `pyproject.toml`. (JSON via stdlib.)
- No new runtime dependency from regex validation (`re` is stdlib).

## Migration / backward compatibility

- Existing CLI invocations (`ctf-poc "<prompt>"`) work unchanged.
- All existing tests continue to pass without modification — `EventConfig` is opt-in.
- The `--max-retries` and `--no-sandbox` CLI flags remain functional; they now override config values when both are present.

## Out of scope (deferred)

- Per-agent CLI overrides (`--architect-model`, `--developer-model`). The `.env.example` hints at these — left for a future focused PR.
- Batch mode (one config → many challenges).
- AI Gateway routing (Pydantic AI Gateway) — orthogonal, would benefit from this work but doesn't block it.
- LangGraph integration for retry orchestration — separate concern (see `DEV.md`).
- Vector RAG — `rag_top_k` field exists and works with keyword retrieval; vector retrieval will use the same knob.
