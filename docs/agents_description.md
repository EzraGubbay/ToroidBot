# Agent Roles in the CTF Challenge Generator

Here is a detailed breakdown of each agent in the CTF challenge generation pipeline and what their exact responsibilities are:

### 1. 🏗️ The Architect (`rag_architect.md`)
*The mastermind that designs the core concept of the challenge.*
* **Inputs:** The user's prompt (e.g., "Medium web SQLi") and **RAG context** (real past CTF challenges pulled from the vector database).
* **Role:** The Architect studies the retrieved source code and solution trajectories of past challenges. Instead of guessing, it adapts and remixes proven vulnerability patterns to design a new challenge. It calibrates the difficulty, chooses the programming language/framework, and decides exactly what the vulnerability should be.
* **Output:** A structured JSON `ChallengeManifest` dictating the blueprint of the challenge (vulnerability class, services, tools required, etc.).

### 2. 📖 The Storyteller (`storyteller.md`)
*The creative writer that builds the player-facing presentation.*
* **Inputs:** The `ChallengeManifest` from the Architect and any constraints from an Event Config (e.g., tone, theme, audience).
* **Role:** Transforms the dry technical blueprint into an engaging CTF scenario. It writes the player-facing README, adds thematic flavor text, and bakes in subtle hints that fit the lore without giving the exploit away. 
* **Output:** A `ChallengeStory` containing the title, description, and narrative elements.

### 3. 💻 The Developer (`ctf_developer.md`)
*The coder that builds the vulnerable application.*
* **Inputs:** The `ChallengeManifest`, the `ChallengeStory`, and RAG context showing how vulnerabilities were embedded in the past.
* **Role:** Writes the actual source code (e.g., `app.py`, `challenge.c`). The Developer's primary directive is to embed **exactly one** exploitable flaw as dictated by the Architect. Crucially, the code must look realistic (like a genuine developer mistake) rather than a planted backdoor, and the Developer must carefully avoid leaving unintended bugs (like accidental directory traversals or race conditions).
* **Output:** A `ChallengeCode` object containing a mapping of filenames to their full source code.

### 4. ⚙️ The DevOps Agent (`devops_infra.md`)
*The infrastructure engineer that packages the challenge.*
* **Inputs:** The `ChallengeManifest` and the `ChallengeCode`.
* **Role:** Packages the code into a secure, deployable Docker container. It selects minimal base images (like `python:3.12-slim` or `ubuntu:24.04`), drops capabilities, runs processes as non-root users, and ensures the flag is locked down securely. For binary exploitation challenges, it sets up tools like `socat` to expose the binary over TCP. 
* **Output:** A `ChallengeInfra` object containing the `Dockerfile` and `docker-compose.yml` (if multi-service).

### 5. 🔓 The Solver (`exploit_solver.md`)
*The hacker that proves the challenge is mathematically solvable.*
* **Inputs:** The manifest, the source code, the infra files, and RAG context containing exploit patterns.
* **Role:** Writes an automated exploit script (usually `solve.py`) designed to attack the generated challenge. It studies the source code to find the vulnerability, formulates an exploit chain, and writes the code to execute it over the network.
* **Output:** The exploit script.

### 6. ⚖️ The Validator (`validator.md` & `docker_runtime.py`)
*The QA engineer that ensures the challenge actually works.*
* **Role:** The Validator doesn't just look at the code; it physically tests it. It performs a two-part review:
  1. **Deterministic Sandbox:** It builds the Docker container, starts it in an isolated internal network, and runs the Solver's exploit script from a sandboxed sibling container (read-only filesystem, no network access). It mathematically proves the challenge works by checking if the exploit successfully captures the flag.
  2. **LLM Review:** It reviews the code for "cheese" solutions or unintended info leaks that the sandbox can't see (e.g., hardcoded passwords left in by mistake).
* **Feedback Loop:** If the challenge fails to build, is unsolvable, or has unintended bugs, the Validator names a **retry target**. It sends the error logs back to the **Developer** (if the code is broken) or the **Solver** (if the exploit is broken) to try again.

---
Together, they form a self-correcting loop: The **Architect** plans it, the **Storyteller** flavors it, the **Developer** codes it, the **DevOps** agent packages it, the **Solver** attacks it, and the **Validator** proves it works.
