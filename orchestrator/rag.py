"""RAG retriever — searches the knowledge base for similar challenges.

Returns rich context (code, solutions, file structures) so agents can
derive implementation decisions from real examples rather than static definitions.

Currently uses keyword matching against challenge metadata.
Future: replace with vector search via pgvector or ChromaDB.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

RAG_DATA_DIR = Path(__file__).resolve().parent.parent / "dataset" / "formated_rag_data"


@functools.lru_cache(maxsize=1)
def _load_challenges() -> tuple[dict, ...]:
    """Load all challenge JSON files from the RAG data directory.

    Cached for the process lifetime — the dataset is read-only at runtime
    and is queried by multiple agents per pipeline run.
    """
    challenges: list[dict] = []
    if not RAG_DATA_DIR.exists():
        return tuple(challenges)
    for path in sorted(RAG_DATA_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                challenges.extend(data)
            else:
                challenges.append(data)
    return tuple(challenges)


def _score_challenge(challenge: dict, query_terms: set[str]) -> int:
    """Score a challenge's relevance to the query terms."""
    searchable = " ".join([
        challenge.get("task_name", ""),
        challenge.get("description", ""),
        challenge.get("category", ""),
        str(challenge.get("difficulty", "")),
        # Also search file contents and solution text for deeper matching
        " ".join(
            f.get("language", "") + " " + f.get("content", "")[:500]
            for f in challenge.get("files", [])
        ),
    ]).lower()

    return sum(1 for term in query_terms if term in searchable)


def _fenced(content: str, lang: str, max_chars: int) -> str:
    """Render content in a fenced code block, neutralizing inner ``` so the fence stays balanced."""
    safe = content[:max_chars].replace("```", "''' '''")
    return f"```{lang}\n{safe}\n```"


def _format_challenge_full(ch: dict) -> str:
    """Format a single challenge with full implementation details."""
    sections = []

    # Header
    sections.append(
        f"### {ch.get('task_name', 'Unknown')} "
        f"[{ch.get('category', '?')}, difficulty {ch.get('difficulty', '?')}]"
    )

    # Description
    desc = ch.get("description", "")
    if desc:
        sections.append(f"**Description:** {desc}")

    # Source files with content — the actual code patterns to learn from
    files = ch.get("files", [])
    for f in files:
        role = f.get("role", "unknown")
        path = f.get("path", "unknown")
        lang = f.get("language", "")
        content = f.get("content", "")
        if content:
            sections.append(f"**File: {path}** (role: {role})")
            sections.append(_fenced(content, lang, max_chars=4000))

    # Solution trajectory — step-by-step exploit approach
    trajectory = ch.get("solution_trajectory", [])
    if trajectory:
        sections.append("**Solution approach:**")
        for step in trajectory:
            action = step.get("action", "")
            command = step.get("command", "")
            if action == "description" and command:
                sections.append(f"- {command[:500]}")
            elif action == "code_snippet" and command:
                lang = step.get("language", "")
                sections.append(_fenced(command, lang, max_chars=1000))

    return "\n\n".join(sections)


def _format_challenge_summary(ch: dict) -> str:
    """Format a challenge as a brief summary with key implementation details."""
    sections = []

    sections.append(
        f"### {ch.get('task_name', 'Unknown')} "
        f"[{ch.get('category', '?')}, difficulty {ch.get('difficulty', '?')}]"
    )

    desc = ch.get("description", "")
    if desc:
        sections.append(f"**Description:** {desc[:300]}")

    # List files and languages used — shows implementation patterns
    files = ch.get("files", [])
    if files:
        file_list = ", ".join(
            f"{f.get('path', '?')} ({f.get('language', '?')})" for f in files
        )
        sections.append(f"**Files:** {file_list}")

    # Brief solution hint
    trajectory = ch.get("solution_trajectory", [])
    for step in trajectory:
        if step.get("action") == "description":
            sections.append(f"**Solution hint:** {step.get('command', '')[:300]}")
            break

    return "\n".join(sections)


def retrieve_similar_challenges(query: str, top_k: int = 3) -> str:
    """Find challenges relevant to the query and return rich context.

    Returns full implementation details (source code, solution trajectories,
    file structures) for the top match, and summaries for the rest. This gives
    agents real examples to derive implementation decisions from.

    Args:
        query: The user's prompt or search terms.
        top_k: Number of results to return.

    Returns:
        Formatted string with challenge details for inclusion in agent prompts.
    """
    challenges = _load_challenges()
    query_terms = set(query.lower().split())

    scored = []
    for ch in challenges:
        score = _score_challenge(ch, query_terms)
        if score > 0:
            scored.append((score, ch))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return "No similar challenges found in the knowledge base."

    results = []

    # Top match gets full detail — code, solutions, everything
    if scored:
        results.append("## Most relevant example (study this for implementation patterns):\n")
        results.append(_format_challenge_full(scored[0][1]))

    # Remaining matches get summaries
    if len(scored) > 1:
        results.append("\n\n## Additional references:\n")
        for _, ch in scored[1:top_k]:
            results.append(_format_challenge_summary(ch))

    return "\n\n".join(results)
