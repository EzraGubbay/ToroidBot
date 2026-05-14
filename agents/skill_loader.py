"""Loads agent persona markdown files from the skills/ directory."""

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_skill(name: str) -> str:
    """Load a skill markdown file by name (without extension).

    Args:
        name: Skill filename stem, e.g. "rag_architect" or "rules".

    Returns:
        The full markdown content of the skill file.

    Raises:
        FileNotFoundError: If the skill file does not exist.
    """
    path = SKILLS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {path}")
    return path.read_text(encoding="utf-8")


def load_rules() -> str:
    """Load the global rules that apply to all agents."""
    return load_skill("rules")


def load_system_prompt(skill_name: str) -> str:
    """Build a full system prompt by combining global rules with an agent's skill file.

    Args:
        skill_name: The agent-specific skill file stem (e.g. "rag_architect").

    Returns:
        Combined system prompt string.
    """
    rules = load_rules()
    skill = load_skill(skill_name)
    return f"{rules}\n\n---\n\n{skill}"
