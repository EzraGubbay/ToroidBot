# DEV.md

Shared project guidance for AI coding assistants. Referenced by CLAUDE.md, GEMINI.md, and AGENTS.md.

## Project Overview

CTF-POC is an AI-powered multi-agent system that generates complete, deployable Capture The Flag challenges. A single prompt produces source code, Dockerfile, solve script, and README — then optionally verifies solvability in a Docker sandbox.

**Input modes:**
1. Prompt-based: specify difficulty, category, and/or topic (e.g., "hard reverse engineering challenge requiring Z3")
2. CVE-based: generate a challenge inspired by a real-world CVE (e.g., "challenge based on CVE-2024-1234")

## Architecture

```
User Prompt → Pydantic-AI Agent Pipeline:
  Architect (RAG) → Developer (Code) → DevOps (Docker) → Solver (Verify) → END
```

- **Architect**: designs the challenge concept using RAG over the knowledge base
- **Developer**: writes the vulnerable source code
- **DevOps**: generates Dockerfile and deployment config
- **Solver**: writes an exploit script and verifies the challenge is solvable

Each agent reads its persona from `.antigravity/skills/*.md` files. Generated challenges are saved to `output/<challenge_name>/`.

## Tech Stack

- **Agent framework**: [Pydantic-AI](https://pydantic.dev/docs/ai/overview/) — Agent class, `@agent.tool` decorators, Pydantic output schemas
- **Model-agnostic**: agents use Pydantic-AI's `<provider>:<model>` string format — the model is a config choice, not hardcoded. Supported providers include Google Gemini, OpenAI (Codex/GPT), Anthropic, and others
- **Multi-provider routing**: [Pydantic AI Gateway](https://pydantic.dev/ai-gateway) for unified access across providers
- **Validation**: Pydantic models for all LLM inputs/outputs
- **Sandbox**: Docker for build/run/verify
- **Config**: `.env` for API keys (provider-specific, e.g., `GEMINI_API_KEY`, `OPENAI_API_KEY`)

Model is selected via environment or CLI flag — never hardcoded in agent definitions. Examples:
- `google-gla:gemini-2.5-flash`
- `openai:codex-mini`
- `openai:gpt-4.1`
- `anthropic:claude-sonnet-4-5`

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
