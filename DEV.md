# DEV.md

Shared project guidance for AI coding assistants. Referenced by CLAUDE.md, GEMINI.md, and AGENTS.md.

## Project Overview

CTF-POC is an AI-powered multi-agent system that generates complete, deployable Capture The Flag challenges. A single prompt produces source code, Dockerfile, solve script, and README — then optionally verifies solvability in a Docker sandbox.

**Input modes:**
1. Prompt-only: `ctf-poc "<prompt>"` — same as before.
2. Prompt + event config: `ctf-poc "<prompt>" --config examples/configs/megactf-2026.yaml`. The event config (YAML or JSON) defines event-wide constraints — flag regex, tone, theme, audience, organizer, forbidden techniques — plus per-agent model routing. CLI flags (`--model`, `--max-retries`, `--no-sandbox`) override config values. See [`examples/configs/`](examples/configs/) for samples and [`docs/superpowers/specs/2026-05-14-event-config-design.md`](docs/superpowers/specs/2026-05-14-event-config-design.md) for the full schema.
3. CVE-based: prompt of the form `Build a challenge inspired by CVE-YYYY-NNNNN`. Reuses the same pipeline; the Architect retrieves CVE-adjacent context from the RAG corpus.

## Architecture

```
User Prompt → Pydantic-AI Agent Pipeline:
  Architect (RAG) → Storyteller → Developer (Code) → DevOps (Docker) → Solver (Exploit) → Validator ─┐
                                                                                                       │
  ┌────────────────────────────────────────────────────────────────────────────────────────────────────┘
  │  Pass? → END
  │  Fail? → feed errors back to Developer (retry loop)
```

- **Architect**: designs the challenge concept (vulnerability type, difficulty, constraints) using RAG over the knowledge base
- **Storyteller**: creates the narrative wrapper — lore, scenario, and flavor text that make the challenge engaging without leaking the solution
- **Developer**: writes the vulnerable source code. The code should contain *only* the intended vulnerability — no accidental bugs, no unintended side channels
- **DevOps**: generates Dockerfile and deployment config
- **Solver**: writes an exploit script that proves the challenge is solvable
- **Validator**: end-of-pipeline quality gate. Builds the container, runs the exploit, confirms the flag is captured, and checks for unintended bugs (e.g., crashes, extra injection points, missing dependencies). On failure, errors are fed back to the Developer for a retry loop

Each agent reads its persona from `skills/*.md` files. Generated challenges are saved to `output/<challenge_name>/`.

### Future: LangGraph for the Validator Loop

The initial pipeline is a linear chain of Pydantic-AI agents. Once the core agents are working, [LangGraph](https://github.com/langchain-ai/langgraph) will be integrated to manage the **Validator → Developer retry loop** as a proper state machine with conditional edges, retry budgets, and cycle detection. This gives us branching, error-feedback loops, and graph visualization that a simple linear chain can't express.

## Tech Stack

- **Agent framework**: [Pydantic-AI](https://pydantic.dev/docs/ai/overview/) — Agent class, `@agent.tool` decorators, Pydantic output schemas
- **Agent framework**: [Pydantic-AI](https://pydantic.dev/docs/ai/overview/) — Agent class, `@agent.tool` decorators, Pydantic output schemas
- **Model-agnostic**: agents use Pydantic-AI's `<provider>:<model>` string format — the model is a config choice, not hardcoded
- **Multi-provider routing**: [OpenRouter](https://pydantic.dev/docs/ai/models/openrouter/) is the team's paid aggregation provider — one key, every model. Direct providers (Gemini AI Studio, OpenAI, Anthropic) remain supported per-agent.
- **Validation**: Pydantic models for all LLM inputs/outputs
- **Sandbox**: Docker for build/run/verify
- **Config**: `.env` for API keys (provider-specific, e.g., `GEMINI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`)

Model is selected via environment or CLI flag — never hardcoded in agent definitions. Examples:
- `google-gla:gemini-2.5-flash`
- `openai:codex-mini`
- `openai:gpt-4.1`
- `anthropic:claude-sonnet-4-5`
- `openrouter:anthropic/claude-sonnet-4-5`
- `openrouter:google/gemini-2.5-flash`

When an `openrouter:` string is routed through `agents.factory.create_agent`, the constructor automatically attaches `app_title="ToroidBot"` to the OpenRouterProvider so the team's OpenRouter dashboard can attribute spend.

**RAG embeddings stay on Gemini AI Studio.** `orchestrator/rag.py` and `indexing/indexer.py` use `google-genai` directly with `gemini-embedding-001` regardless of agent provider routing — OpenRouter does not proxy Gemini embeddings, and switching embedders mid-corpus would invalidate the indexed vectors.

## Domain Knowledge Requirements

This project sits at the intersection of three domains. AI assistants working in this codebase should understand the context behind each layer:

### AI & Orchestration
- Agents are stateful — the pipeline passes a state object between nodes so downstream agents (e.g., Developer) can reference upstream outputs (e.g., Architect's manifest). State must survive error/retry loops.
- All LLM outputs are validated via Pydantic schemas with structured output. Malformed responses trigger retries, not crashes.
- RAG retrieval feeds the Architect real challenge examples. Future direction: vector search via `pgvector` or ChromaDB, potentially GraphRAG with ego-graph extraction.
- Agent personas (`skills/*.md`) use Chain-of-Thought prompting to force step-by-step reasoning rather than one-shot code generation.

### Offensive Security
- The system must encode vulnerability patterns (buffer overflows, UAF, SQLi, XSS, SSTI, JWT bypass) across C, Python, and JavaScript.
- The Solver agent automates exploitation using tools like `pwntools` (pwn/rev) and `requests` (web). Generated solve scripts must be runnable, not pseudocode.
- Advanced challenges use constraint solvers (Z3) and symbolic execution (angr) — the RAG knowledge base includes examples of these.
- CVE-based mode requires distilling a CVE report into a challenge concept the AI can implement as an isolated, safe-to-deploy scenario.

### Infrastructure & Sandboxing
- Generated challenges are packaged in Docker containers. The DevOps agent writes `Dockerfile` and optionally `docker-compose.yml` for multi-service setups (frontend + backend + database).
- Sandbox verification builds the container, runs the solve script against it, and checks for `CTF{` in stdout. Containers are torn down after verification.
- Security hardening matters — generated containers must not allow breakout. Rootless containers and namespace isolation are preferred.
- Future scale path: Go-based queue workers, AWS SQS/ECS Fargate for batch generation.

## RAG Knowledge Base

`dataset/formated_rag_data/` contains ~35 challenge JSON files with this schema:

```
task_name, description, category, difficulty, flag,
source_files[], files[{role, path, language, content}],
solution_trajectory[{role, action, command, source_file}],
metadata{easy_prompt, hard_prompt, subtasks[]}
```

Categories span crypto, web, misc, rev, pwn. Difficulties range from "very easy" to "hard".

`dataset/format_challenges.py` converts raw challenge directories into this format:
```bash
python dataset/format_challenges.py <base_dir> --output-dir <out_dir> [--combine-name combined.json]
```

## Commands

```bash
# Environment setup
python -m venv venv
source venv/bin/activate
pip install pydantic-ai pydantic python-dotenv

# Configure API keys (add whichever providers you use)
echo "GEMINI_API_KEY=your-key" >> .env
echo "OPENAI_API_KEY=your-key" >> .env

# Run the pipeline (once built)
python -m orchestrator.main "Create a medium web challenge about SQL injection"
```

## Documentation Checklist (developer rules)

When making substantial changes (API surface, routes, environment variables, public behaviour, or deployment flow), update `README.md` and include a short note in the PR description. Minimal checklist:

- **README.md**: update endpoints, required env vars, how to run the dev server, and any test/run caveats.
- **DEV.md**: add any developer-only commands or steps that depend on new services (DB, vector store, provider keys).
- **Tests**: add or update targeted unit tests for the changed surface. If changes require external services, document which tests need provisioning and how to run them locally.
- **Changelog / PR description**: include a concise list of breaking/important changes so reviewers can validate documentation coverage.

Follow this checklist before merging PRs that change behavior other developers will rely on.
