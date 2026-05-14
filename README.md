# 🏴 CTF-POC: AI-Powered CTF Challenge Generator

> **Hackathon Project** — An autonomous multi-agent system that designs, codes, deploys, and verifies Capture The Flag challenges using LangGraph + Google Gemini.

---

## 🎯 What We're Building

A pipeline where you type a single prompt like:
```
"Make a hard reverse engineering challenge that requires Z3 to solve"
```
…and the system automatically:
1. **Designs** the challenge concept (vulnerability, lore, difficulty)
2. **Writes** the vulnerable source code (C, Python, etc.)
3. **Packages** it in Docker for deployment
4. **Generates** an exploit script that proves it's solvable
5. **Saves** everything to a ready-to-deploy folder

---

## 🏗️ Architecture

```
User Prompt
    │
    ▼
┌──────────────────────────────────────────────────────┐
│                   LangGraph Workflow                 │
│                                                      │
│  ┌───────────┐   ┌───────────┐   ┌────────┐   ┌───────────┐ │
│  │ Architect │──▶│ Developer │──▶│ DevOps │──▶│  Solver   │ │
│  │  (RAG)    │   │  (Code)   │   │(Docker)│   │ (Verify)  │ │
│  └─────┬─────┘   └───────────┘   └────────┘   └───────────┘ │
│        │                                                     │
│   ┌────┴─────┐                                               │
│   │Knowledge │                                               │
│   │  Base    │                                               │
│   └──────────┘                                               │
└──────────────────────────────────────────────────────┘
    │
    ▼
  output/<challenge_name>/
    ├── source code
    ├── Dockerfile
    ├── solve.py
    └── README.md
```

Each agent reads its persona from `.antigravity/skills/*.md` — no hardcoded system prompts.

---

## 📋 Hackathon Task List

### Phase 0: Environment Setup (15 min)
- [ ] Clone the repo / open in IDX
- [ ] Set up Python virtual environment
- [ ] `pip install langgraph google-generativeai pydantic python-dotenv`
- [ ] Add Gemini API key to `.env`
- [ ] Verify basic Gemini connectivity with a test call

### Phase 1: Get the Pipeline Running End-to-End (1.5 hr)
> **Goal:** Type a prompt → get a folder with source code + Dockerfile + solve script.

- [ ] **1.1** Verify `api_callers.py` loads skill files and calls Gemini correctly
- [ ] **1.2** Test `architect_node.py` — does it return valid JSON matching `ChallengeManifest`?
- [ ] **1.3** Test `developer_node.py` — does it return a dict of filename → code?
- [ ] **1.4** Test `infra_node.py` — does it return a valid Dockerfile?
- [ ] **1.5** Test `verify_node.py` — does it return a solve.py script?
- [ ] **1.6** Run the full pipeline via `python -m orchestrator.main "simple web SQLi challenge"`
- [ ] **1.7** Verify output files are saved correctly to `output/`

### Phase 2: Knowledge Base & RAG (45 min)
> **Goal:** Populate the knowledge base so the Architect produces better challenges.

- [ ] **2.1** Import challenges from `ico-tasks` into `data/challenges.json`
- [ ] **2.2** Import our hand-crafted examples (Z3 Magic, Vault Maze)
- [ ] **2.3** Verify RAG retrieval: prompt for "reverse engineering" → Architect references rev challenges
- [ ] **2.4** (Stretch) Replace keyword matching with ChromaDB vector search

### Phase 3: Docker Sandbox Verification (1 hr)
> **Goal:** Automatically build the challenge in Docker, run the solver against it, confirm the flag is captured.

- [ ] **3.1** Implement `docker_runtime.py` — write files to temp dir, `docker build`, `docker run`
- [ ] **3.2** Run `solve.py` against the container (web: HTTP to localhost, pwn: pwntools remote)
- [ ] **3.3** Parse solver output — check if `CTF{` appears in stdout
- [ ] **3.4** Tear down the container after verification
- [ ] **3.5** If verification fails, feed the error back to the Developer node (self-reflection loop)

### Phase 4: Challenge Quality (45 min)
> **Goal:** Ensure generated challenges are actually good, not trivial or broken.

- [ ] **4.1** Refine skill prompts (`rag_architect.md`, `ctf_developer.md`) based on output quality
- [ ] **4.2** Add Pydantic validation in nodes — reject malformed LLM responses and retry
- [ ] **4.3** Generate at least 3 different challenge categories successfully:
  - [ ] Web (e.g., SQLi, SSTI, JWT bypass)
  - [ ] Rev (e.g., Z3 constraints, angr maze)
  - [ ] Crypto (e.g., RSA small-e, AES nonce reuse)
- [ ] **4.4** Manually test at least 1 generated challenge end-to-end (build → solve → flag)

### Phase 5: Demo Polish (30 min)
> **Goal:** Make it look impressive for the judges.

- [ ] **5.1** Clean up terminal output (progress bars, emoji, timing)
- [ ] **5.2** Prepare 2-3 pre-cooked prompts that produce impressive output
- [ ] **5.3** Write a one-slide summary of the architecture
- [ ] **5.4** (Stretch) Add a simple Streamlit/Gradio web UI front-end
- [ ] **5.5** (Stretch) Add a "batch mode" — generate 5 challenges at once from a category list

---

## 🚀 Quick Start

```bash
# 1. Setup
cd ctf-poc
python -m venv venv
source venv/bin/activate  # or .\venv\Scripts\Activate.ps1 on Windows
pip install langgraph google-generativeai pydantic python-dotenv

# 2. Configure
echo "GEMINI_API_KEY=your-key-here" > .env

# 3. Run
python -m orchestrator.main "Create a medium web challenge about SQL injection"

# 4. Check output
ls output/
```

---

## 📁 Project Structure

```
ctf-poc/
├── .antigravity/                # Agent Personas (editable markdown)
│   ├── rules.md                 # Global constraints
│   └── skills/
│       ├── rag_architect.md     # How to design challenges
│       ├── ctf_developer.md     # How to write vulnerable code
│       ├── devops_infra.md      # How to write Dockerfiles
│       └── exploit_solver.md    # How to write exploits
│
├── .idx/dev.nix                 # Google IDX environment (optional)
├── pyproject.toml               # Dependencies
├── .env                         # API keys (not committed)
│
├── orchestrator/
│   ├── main.py                  # Entry point
│   └── manager.py               # Knowledge base I/O + file saving
│
├── graph/                       # LangGraph Engine
│   ├── state.py                 # CTFState definition
│   ├── workflow.py              # Graph: architect→developer→infra→verify→END
│   └── nodes/
│       ├── architect_node.py    # Concept design (RAG)
│       ├── developer_node.py    # Source code generation
│       ├── infra_node.py        # Dockerfile generation
│       └── verify_node.py       # Exploit + verification
│
├── agents/
│   ├── schemas.py               # Pydantic output schemas
│   └── api_callers.py           # Skill loader + Gemini API wrapper
│
├── sandbox/
│   └── docker_runtime.py        # Docker build/run/verify
│
├── data/
│   └── challenges.json          # Knowledge base of past challenges
│
└── output/                      # Generated challenges saved here
```

---

## 🔑 Key Design Decisions

| Decision | Why |
|----------|-----|
| **Agent personas in `.md` files** | Tweak prompts without touching code. Judges can read them. |
| **LangGraph** | Gives us a real state machine with retries, branching, and visualization. |
| **Gemini 2.5 Flash** | Fast, cheap, native JSON mode — ideal for structured multi-agent output. |
| **Pydantic schemas** | Prevents "LLM returned garbage" crashes. Validates every response. |
| **Docker sandbox** | Proves the challenge actually works, not just that the code looks right. |

---

## ⚡ Stretch Goals (if time permits)
- **Self-reflection loop:** If the solver fails, feed the error back to the Developer automatically.
- **Streamlit UI:** Web interface instead of CLI.
- **Batch generation:** Generate an entire CTF competition (5-10 challenges) from a single config file.
- **ChromaDB RAG:** Semantic search over the knowledge base instead of keyword matching.
- **LangGraph visualization:** Export the graph as an image for the presentation.

I want the readme to include the following things:
First I want the input to the system to have a couple of possible modes
1. I want a DIFFICULTY challenge for category X related to Y (Difficulty and/or Category and/or specific vulnerability/topic/tool)
2. I want a challenge related to CVE xxxx-yyyy (Challenges inspired by real world vulnerabilities. 
The first method It's more important than the second but add both of them.

Next I want to have clear to do for the it doesn't necessarily need to based on the current challenges but using challenges for the rag I also want to improve the skills we have and have a list of skills we should get like docker G uh setting up servers whatever, I want to know if we should also have templates Different challenges or if we should use the RAG for that. And then any other thing you think we should add