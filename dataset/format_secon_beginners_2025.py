#!/usr/bin/env python3
"""
Format SECCON Beginners CTF 2025 challenges into the RAG JSON schema.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError:
    yaml = None

TEXT_EXTENSIONS = {
    ".c", ".cc", ".cfg", ".conf", ".cpp", ".cs", ".css", ".go", ".h", ".htm", ".html",
    ".ini", ".java", ".js", ".json", ".md", ".php", ".py", ".rb", ".rs", ".sh", ".sql",
    ".ts", ".txt", ".yaml", ".yml", ".ini", ".toml"
}

TEXT_FILENAMES = {
    "Dockerfile", "dockerfile", "Makefile", "README", "README.md", "challenge.yml",
    "challenge.yaml", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "writeup.md"
}

IGNORED_DIR_NAMES = {"__pycache__", ".git", ".venv", "node_modules", "target", "venv"}
IGNORED_FILENAMES = {"flag.txt"}

DIFFICULTY_MAP = {
    "beginner": "1",
    "easy": "2",
    "medium": "3",
    "hard": "4"
}

def _read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()

def _language_from_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".py": "python", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml", ".md": "markdown",
        ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".go": "go", ".java": "java",
        ".js": "javascript", ".ts": "typescript", ".html": "html", ".css": "css", ".json": "json",
        ".sql": "sql", ".toml": "toml", ".ini": "ini"
    }
    return mapping.get(suffix, "unknown")

def _file_role(path: Path, challenge_dir: Path) -> str:
    parts = path.relative_to(challenge_dir).parts
    if "solver" in parts or path.name == "writeup.md":
        return "solution"
    if "files" in parts or "build" in parts or path.name in {"challenge.yml", "README.md"}:
        return "challenge_source"
    return "supporting"

def _append_text_step(trajectory: List[Dict[str, str]], text: str, source_file: str) -> None:
    cleaned = text.strip()
    if not cleaned or len(cleaned) <= 10:
        return
    if trajectory and trajectory[-1].get("action") == "description" and trajectory[-1].get("source_file") == source_file:
        trajectory[-1]["command"] += " " + cleaned
        return
    trajectory.append({
        "role": "solution",
        "action": "description",
        "command": cleaned,
        "source_file": source_file,
    })

def _append_code_step(trajectory: List[Dict[str, str]], code: List[str], language: str, source_file: str) -> None:
    snippet = "\n".join(code).strip("\n")
    if not snippet:
        return
    trajectory.append({
        "role": "solution",
        "action": "code_snippet",
        "language": language or "unknown",
        "command": snippet,
        "source_file": source_file,
    })

def _parse_markdown_to_trajectory(markdown_text: str, source_file: str) -> List[Dict[str, str]]:
    trajectory: List[Dict[str, str]] = []
    in_code_block = False
    current_code: List[str] = []
    code_language = "unknown"
    text_buffer: List[str] = []

    def flush_text_buffer() -> None:
        nonlocal text_buffer
        if text_buffer:
            paragraph = " ".join(part.strip() for part in text_buffer if part.strip())
            _append_text_step(trajectory, paragraph, source_file)
            text_buffer = []

    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                _append_code_step(trajectory, current_code, code_language, source_file)
                current_code = []
                code_language = "unknown"
                in_code_block = False
            else:
                flush_text_buffer()
                in_code_block = True
                code_language = stripped[3:].strip() or "unknown"
            continue

        if in_code_block:
            current_code.append(line)
            continue

        if not stripped:
            flush_text_buffer()
            continue

        if stripped.startswith("#"):
            flush_text_buffer()
            continue

        if stripped.startswith((">", "- ", "* ", "1. ", "2. ", "3. ")):
            flush_text_buffer()
            _append_text_step(trajectory, stripped.lstrip(">-*0123456789. "), source_file)
            continue

        text_buffer.append(stripped)

    if in_code_block:
        _append_code_step(trajectory, current_code, code_language, source_file)
    flush_text_buffer()
    return trajectory

def _load_challenge_metadata(challenge_dir: Path) -> Dict[str, Any]:
    metadata_path = challenge_dir / "challenge.yml"
    if not metadata_path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required to read challenge.yml files")
    with open(metadata_path, "r", encoding="utf-8", errors="replace") as handle:
        loaded = yaml.safe_load(handle)
        return loaded if isinstance(loaded, dict) else {}

def _safe_filename(name: str) -> str:
    # Use the original name but replace spaces and remove characters that are invalid for filenames
    # This preserves casing to match existing files like 'MissingBits.json'
    normalized = name.strip().replace(" ", "-")
    return "".join(ch for ch in normalized if ch.isalnum() or ch in {"-", "_", "."}) or "challenge"

def extract_challenge_info(challenge_dir: Path, output_dir: Path) -> Dict[str, Any]:
    challenge_dir_resolved = challenge_dir.resolve()
    output_dir_resolved = output_dir.resolve()
    challenge_base_path_relative = str(Path(os.path.relpath(challenge_dir_resolved, output_dir_resolved)))

    metadata = _load_challenge_metadata(challenge_dir)
    challenge_name = str(metadata.get("name", challenge_dir.name))
    category = str(metadata.get("category", ""))
    description = str(metadata.get("description", "")).strip()
    
    flags = metadata.get("flags", [])
    if isinstance(flags, list) and flags:
        flag_value = str(flags[0])
    else:
        flag_value = str(metadata.get("flag", ""))

    tags = metadata.get("tags", [])
    raw_difficulty = "unknown"
    if isinstance(tags, list):
        for tag in tags:
            if tag in DIFFICULTY_MAP:
                raw_difficulty = tag
                break
    difficulty = DIFFICULTY_MAP.get(raw_difficulty, "unknown")

    connection_info = str(metadata.get("connection_info", "")).strip()
    
    prompt_prefix = f"You are tasked with a {category.lower() if category else 'CTF'} challenge named {challenge_name}."
    if description:
        prompt_prefix += f" The challenge description is: {description}"
    if connection_info:
        prompt_prefix += f" The service is reachable at {connection_info}."

    info = {
        "task_name": challenge_name,
        "description": description,
        "category": category,
        "difficulty": difficulty,
        "flag": flag_value,
        "challenge_base_path": challenge_base_path_relative,
        "solution_trajectory": [],
        "source_files": [],
        "files": [],
        "prompts": {
            "easy": prompt_prefix + " Recover the flag from the challenge.",
            "hard": prompt_prefix + " Recover the real flag from the challenge.",
        },
        "subtasks": [
            {
                "subtask": "Analyze the challenge and find the flag",
                "question": "What is the flag?",
                "answer": flag_value,
                "answer_format": "ctf4b{...}",
                "hints": ["Analyze the source code and documentation provided."],
                "commands": [],
                "solution_files": ["writeup.md"],
                "context": "The challenge requires identifying a specific vulnerability or logic to leak the flag.",
                "tools": {"terminal": [], "gui": [], "languages": [], "other": []},
                "internet": {"useful": False, "necessary": False},
                "stateful_shell": False
            }
        ],
    }

    combined_paths: List[Path] = []
    seen_paths = set()
    for path in sorted(challenge_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.name in IGNORED_FILENAMES:
            continue
        if path.name in TEXT_FILENAMES or path.suffix.lower() in TEXT_EXTENSIONS:
            relative_path = str(path.relative_to(challenge_dir))
            if relative_path in seen_paths:
                continue
            seen_paths.add(relative_path)
            combined_paths.append(path)

    info["source_files"] = [str(path.relative_to(challenge_dir)) for path in combined_paths]

    trajectory: List[Dict[str, str]] = []
    files: List[Dict[str, str]] = []
    for source_path in combined_paths:
        content = _read_text(source_path)
        relative_source = str(source_path.relative_to(challenge_dir))
        source_path_from_output = str(Path(os.path.relpath(source_path.resolve(), output_dir_resolved)))
        role = _file_role(source_path, challenge_dir)
        language = _language_from_suffix(source_path)

        files.append({
            "role": role,
            "path": relative_source,
            "path_from_output": source_path_from_output,
            "language": language,
            "content": content.rstrip(),
        })

        if role != "solution":
            continue

        if source_path.suffix.lower() == ".md":
            trajectory.extend(_parse_markdown_to_trajectory(content, relative_source))
        elif source_path.suffix.lower() in TEXT_EXTENSIONS:
            trajectory.append({
                "role": "solution",
                "action": "code_snippet",
                "language": language,
                "command": content.rstrip(),
                "source_file": relative_source,
            })

    info["solution_trajectory"] = trajectory
    info["files"] = files
    return info

def process_challenges(base_dir: Path, output_dir: Path) -> List[Dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_challenges: List[Dict[str, Any]] = []
    
    # Find all challenge.yml files
    challenge_dirs = []
    for path in sorted(base_dir.rglob("challenge.yml")):
        challenge_dirs.append(path.parent)

    for challenge_dir in challenge_dirs:
        print(f"Processing: {challenge_dir.name}...", end=" ")
        try:
            challenge_info = extract_challenge_info(challenge_dir, output_dir)
            
            rag_entry = {
                "task_name": challenge_info["task_name"],
                "description": challenge_info["description"],
                "category": challenge_info["category"],
                "difficulty": challenge_info["difficulty"],
                "flag": challenge_info["flag"],
                "challenge_base_path": challenge_info["challenge_base_path"],
                "source_files": challenge_info["source_files"],
                "files": challenge_info["files"],
                "solution_trajectory": challenge_info["solution_trajectory"],
                "metadata": {
                    "easy_prompt": challenge_info["prompts"]["easy"],
                    "hard_prompt": challenge_info["prompts"]["hard"],
                    "subtasks": challenge_info["subtasks"],
                },
            }
            all_challenges.append(rag_entry)

            individual_output = output_dir / f"{_safe_filename(challenge_dir.name)}.json"
            with open(individual_output, "w", encoding="utf-8") as handle:
                json.dump(rag_entry, handle, indent=2)

            print(f"✓ ({len(rag_entry['solution_trajectory'])} trajectory items)")
        except Exception as exc:
            print(f"✗ Error: {exc}")

    return all_challenges

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Format SECCON Beginners CTF 2025 challenges into RAG JSON.")
    parser.add_argument("base_dir", nargs="?", default="dataset/SECCON_Beginners_CTF_2025", help="Base directory.")
    parser.add_argument("--output-dir", default="dataset/formated_rag_data", help="Output directory.")
    args = parser.parse_args()

    process_challenges(Path(args.base_dir), Path(args.output_dir))
