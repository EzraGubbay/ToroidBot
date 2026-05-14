#!/usr/bin/env python3
"""
Format pbCTF 2020 challenge folders into the RAG JSON schema.

The corpus is organized as category folders (crypto, misc, pwn, rev, web)
containing one folder per challenge. Each challenge usually provides a README
with metadata plus a mix of source, solution, and supporting files.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".php",
    ".pl",
    ".py",
    ".rb",
    ".rs",
    ".sage",
    ".sby",
    ".sh",
    ".sql",
    ".sv",
    ".s",
    ".swift",
    ".toml",
    ".ts",
    ".txt",
    ".v",
    ".xml",
    ".yaml",
    ".yml",
}

TEXT_FILENAMES = {
    "Dockerfile",
    "README",
    "README.md",
    "SOLUTION.md",
    "WRITEUP.md",
    "challenge.json",
    "challenge.yaml",
    "challenge.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
}

IGNORED_DIR_NAMES = {"__pycache__", ".git", ".venv", "node_modules", "target", "venv"}
IGNORED_FILENAMES = {"flag.txt"}
INTERESTING_STEMS = {
    "solve",
    "solver",
    "solution",
    "writeup",
    "exploit",
    "exp",
    "readme",
    "challenge",
    "main",
    "patch",
}


def _read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _safe_filename(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "-")
    return "".join(ch for ch in normalized if ch.isalnum() or ch in {"-", "_"}) or "challenge"


def _language_from_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix == ".sh":
        return "bash"
    if suffix == ".sage":
        return "sage"
    if suffix in {".v", ".sv"}:
        return "verilog"
    if suffix == ".rs":
        return "rust"
    if suffix == ".toml":
        return "toml"
    if suffix in {".yml", ".yaml"}:
        return "yaml"
    if suffix == ".md":
        return "markdown"
    if suffix in {".c", ".h"}:
        return "c"
    if suffix in {".cc", ".cpp", ".cxx"}:
        return "cpp"
    if suffix == ".go":
        return "go"
    if suffix == ".java":
        return "java"
    if suffix in {".js", ".jsx"}:
        return "javascript"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    if suffix == ".html":
        return "html"
    if suffix == ".css":
        return "css"
    if suffix == ".sql":
        return "sql"
    if suffix == ".txt":
        return "text"
    if suffix == ".json":
        return "json"
    return "unknown"


def _file_role(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    stem = path.stem.lower()
    name = path.name.lower()

    if stem in {"solve", "solver", "solution", "exploit", "exp", "writeup"}:
        return "solution"
    if name in {"solution.py", "solve.py", "exp.py", "exploit.py", "writeup.py", "solution.sage", "soln.py"}:
        return "solution"
    if any(part in {"solution", "solve", "soln", "writeup"} for part in parts):
        return "solution"
    if path.name in TEXT_FILENAMES or any(part in {"challenge", "src", "dist", "docs", "public", "metadata"} for part in parts):
        return "challenge_source"
    return "supporting"


def _append_text_step(trajectory: List[Dict[str, str]], text: str, source_file: str) -> None:
    cleaned = text.strip()
    if not cleaned or len(cleaned) <= 10:
        return
    if trajectory and trajectory[-1].get("action") == "description" and trajectory[-1].get("source_file") == source_file:
        trajectory[-1]["command"] += " " + cleaned
        return
    trajectory.append(
        {
            "role": "solution",
            "action": "description",
            "command": cleaned,
            "source_file": source_file,
        }
    )


def _append_code_step(
    trajectory: List[Dict[str, str]],
    code: List[str],
    language: str,
    source_file: str,
) -> None:
    snippet = "\n".join(code).strip("\n")
    if not snippet:
        return
    trajectory.append(
        {
            "role": "solution",
            "action": "code_snippet",
            "language": language or "unknown",
            "command": snippet,
            "source_file": source_file,
        }
    )


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


def _parse_text_file_to_trajectory(text: str, source_file: str) -> List[Dict[str, str]]:
    if "```" in text or "# " in text:
        return _parse_markdown_to_trajectory(text, source_file)

    cleaned = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return [
        {
            "role": "solution",
            "action": "description",
            "command": cleaned,
            "source_file": source_file,
        }
    ] if cleaned else []


def _discover_challenge_dirs(base_dir: Path) -> List[Path]:
    challenge_dirs: List[Path] = []
    if not base_dir.exists():
        return challenge_dirs

    for category_dir in sorted(base_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        for challenge_dir in sorted(category_dir.iterdir()):
            if not challenge_dir.is_dir():
                continue
            if (challenge_dir / "README.md").is_file():
                challenge_dirs.append(challenge_dir)
    return challenge_dirs


def _gather_text_files(challenge_dir: Path) -> List[Path]:
    candidates: List[Path] = []
    for path in sorted(challenge_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part.lower() in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.name in IGNORED_FILENAMES:
            continue
        suffix = path.suffix.lower()
        if path.name in TEXT_FILENAMES or suffix in TEXT_EXTENSIONS or path.stem.lower() in INTERESTING_STEMS:
            candidates.append(path)
    return candidates


def _parse_list_block(readme_text: str, heading_pattern: str) -> List[str]:
    lines = readme_text.splitlines()
    results: List[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if re.match(heading_pattern, stripped, flags=re.IGNORECASE):
            collecting = True
            if ":" in stripped:
                inline_value = stripped.split(":", 1)[1].strip()
                if inline_value:
                    results.append(inline_value)
            continue
        if collecting and re.match(r"^(\*\*|##|#)\s*[A-Za-z].*", stripped):
            break
        if collecting and stripped.startswith(("* ", "- ")):
            item = stripped[2:].strip()
            if item:
                results.append(item)
    return results


def _extract_title(readme_text: str, fallback: str) -> str:
    for line in readme_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title and title.lower() not in {"challenge name", "author's writeup", "author writeup", "writeup", "solution"}:
                return title
    return fallback


def _extract_section_text(readme_text: str, section_name: str) -> str:
    lines = readme_text.splitlines()
    collecting = False
    buffer: List[str] = []
    section_pattern = re.compile(rf"^(\*\*|##|#)\s*{re.escape(section_name)}\b", flags=re.IGNORECASE)
    for line in lines:
        stripped = line.rstrip()
        if section_pattern.match(stripped):
            collecting = True
            if ":" in stripped:
                inline_value = stripped.split(":", 1)[1].strip()
                if inline_value:
                    buffer.append(inline_value)
            continue
        if collecting:
            if re.match(r"^(\*\*|##|#)\s*[A-Za-z].*", stripped.strip()):
                break
            if stripped.strip():
                buffer.append(stripped.strip())
    return " ".join(buffer).strip()


def _extract_fields_from_readme(readme_text: str, challenge_dir: Path) -> Dict[str, Any]:
    title = _extract_title(readme_text, challenge_dir.name)
    if title.lower() in {"challenge name", "author's writeup", "author writeup", "writeup", "solution"}:
        title = challenge_dir.name.replace("_", " ").replace("-", " ").title()

    category = challenge_dir.parent.name.lower()

    description = _extract_section_text(readme_text, "Description")
    hints = _parse_list_block(readme_text, r"^(\*\*Hints\*\*|##\s*Hints|#\s*Hints)\s*:?")
    public_files = _parse_list_block(readme_text, r"^(\*\*Public Files\*\*|\*\*Distribute\*\*|##\s*Public Files|#\s*Public Files|##\s*Distribute|#\s*Distribute)\s*:?")
    solution_text = _extract_section_text(readme_text, "Solution")

    return {
        "title": title,
        "category": category,
        "description": description,
        "hints": hints,
        "public_files": public_files,
        "solution_text": solution_text,
    }


def _extract_flag(challenge_dir: Path, readme_text: str) -> str:
    patterns = [r"pbctf\{[^}]+\}", r"flag\{[^}]+\}"]
    text_sources = [readme_text]
    for path in sorted(challenge_dir.rglob("*")):
        if not path.is_file() or path.name in IGNORED_FILENAMES:
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in TEXT_FILENAMES:
            continue
        try:
            text_sources.append(_read_text(path))
        except OSError:
            continue

    for text in text_sources:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(0)
    return ""


def _subtask(
    subtask: str,
    question: str,
    answer: str,
    answer_format: str,
    hints: List[str],
    commands: List[str],
    context: str,
    tools_languages: List[str],
    solution_files: Optional[List[str]] = None,
    annotator_note: str = "",
) -> Dict[str, Any]:
    return {
        "subtask": subtask,
        "question": question,
        "answer": answer,
        "answer_format": answer_format,
        "hints": hints,
        "commands": commands,
        "solution_files": solution_files or [],
        "annotator_note": annotator_note,
        "context": context,
        "tools": {
            "terminal": [],
            "gui": [],
            "languages": tools_languages,
            "other": [],
        },
        "internet": {
            "useful": False,
            "necessary": False,
        },
        "stateful_shell": False,
    }


def _build_prompts(title: str, category: str, description: str, public_files: List[str], solution_files: List[str]) -> Dict[str, str]:
    prompt_prefix = f"You are tasked with a {category or 'CTF'} challenge named {title}."
    if description:
        prompt_prefix += f" The challenge description is: {description}"
    if public_files:
        prompt_prefix += f" You are given the following public files: {', '.join(public_files)}."
    if solution_files:
        prompt_prefix += f" Solution artifacts include: {', '.join(solution_files)}."
    return {
        "easy": prompt_prefix + " Recover the flag from the challenge.",
        "hard": prompt_prefix + " Recover the real flag from the challenge.",
    }


def _build_subtasks(
    title: str,
    category: str,
    description: str,
    hints: List[str],
    public_files: List[str],
    solution_files: List[str],
    flag: str,
) -> List[Dict[str, Any]]:
    answer_format = "pbctf{************************}"
    if flag.startswith("flag{"):
        answer_format = "flag{************************}"

    source_hint = public_files[:3] if public_files else ["README.md"]
    solution_hint = solution_files[:3] if solution_files else ["README.md"]
    hint_text = hints[:3] if hints else ["Read the README and inspect the provided source files."]

    return [
        _subtask(
            "Survey the challenge surface.",
            f"Which files are explicitly called out for {title}?",
            ", ".join(public_files) if public_files else "provided source files",
            "comma-separated file names",
            hint_text,
            ["cat README.md"],
            f"The challenge lives in the {category} corpus and the README identifies the public artifacts.",
            ["markdown"],
            ["README.md"],
        ),
        _subtask(
            "Follow the intended solve path.",
            "Which artifact most directly explains the intended solve path?",
            ", ".join(solution_files) if solution_files else "README.md solution section",
            "comma-separated file names or a short path",
            ["Inspect the README solution section and any solve scripts."] + hint_text[:2],
            ["cat README.md"] + [f"cat {path}" for path in solution_hint if path != "README.md"],
            "The repository usually contains either a solve script, a solution directory, or an author writeup that captures the intended exploit path.",
            sorted({"markdown"} | { _language_from_suffix(Path(path)) for path in solution_files[:3] }) if solution_files else ["markdown"],
            solution_hint,
        ),
        _subtask(
            "Recover the flag.",
            "What's the flag?",
            flag or "flag not explicitly exposed in the extracted files",
            answer_format,
            hint_text,
            ["cat README.md"] + [f"cat {path}" for path in solution_hint if path != "README.md"],
            "Use the README clues and solution artifacts to reconstruct the final flag.",
            sorted({"markdown"} | { _language_from_suffix(Path(path)) for path in solution_files[:3] }) if solution_files else ["markdown"],
            solution_hint,
        ),
    ]


def _extract_challenge_info(challenge_dir: Path, output_dir: Path) -> Dict[str, Any]:
    challenge_dir_resolved = challenge_dir.resolve()
    output_dir_resolved = output_dir.resolve()
    challenge_base_path_relative = str(Path(os.path.relpath(challenge_dir_resolved, output_dir_resolved)))

    readme_path = challenge_dir / "README.md"
    readme_text = _read_text(readme_path) if readme_path.exists() else ""
    fields = _extract_fields_from_readme(readme_text, challenge_dir)

    title = fields["title"]
    category = fields["category"]
    description = fields["description"]
    hints = fields["hints"]
    public_files = fields["public_files"]
    solution_text = fields["solution_text"]
    flag = _extract_flag(challenge_dir, readme_text)

    solution_files: List[str] = []
    for path in sorted(challenge_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part.lower() in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.name in IGNORED_FILENAMES:
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in TEXT_FILENAMES:
            continue
        role = _file_role(path)
        if role == "solution":
            solution_files.append(str(path.relative_to(challenge_dir)))

    prompts = _build_prompts(title, category, description, public_files, solution_files)

    info = {
        "task_name": title,
        "description": description,
        "category": category,
        "difficulty": "unknown",
        "flag": flag,
        "challenge_base_path": challenge_base_path_relative,
        "solution_trajectory": [],
        "source_files": [],
        "files": [],
        "prompts": prompts,
        "subtasks": _build_subtasks(title, category, description, hints, public_files, solution_files, flag),
    }

    combined_paths: List[Path] = []
    seen_paths = set()
    for path in _gather_text_files(challenge_dir):
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
        role = _file_role(source_path)
        language = _language_from_suffix(source_path)

        files.append(
            {
                "role": role,
                "path": relative_source,
                "path_from_output": source_path_from_output,
                "language": language,
                "content": content.rstrip(),
            }
        )

        if role != "solution":
            continue

        if source_path.suffix.lower() == ".md":
            trajectory.extend(_parse_markdown_to_trajectory(content, relative_source))
        elif source_path.suffix.lower() in TEXT_EXTENSIONS:
            if source_path.suffix.lower() == ".txt" and "```" not in content and len(content.splitlines()) <= 6:
                trajectory.extend(_parse_text_file_to_trajectory(content, relative_source))
            else:
                trajectory.append(
                    {
                        "role": "solution",
                        "action": "code_snippet",
                        "language": language,
                        "command": content.rstrip(),
                        "source_file": relative_source,
                    }
                )

    if solution_text and not trajectory:
        trajectory.extend(_parse_markdown_to_trajectory(solution_text, "README.md"))

    info["solution_trajectory"] = trajectory
    info["files"] = files
    return info


def format_for_rag(challenge_info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_name": challenge_info["task_name"],
        "description": challenge_info["description"],
        "category": challenge_info["category"],
        "difficulty": challenge_info.get("difficulty", "unknown"),
        "flag": challenge_info.get("flag", ""),
        "challenge_base_path": challenge_info.get("challenge_base_path", ""),
        "source_files": challenge_info.get("source_files", []),
        "files": challenge_info.get("files", []),
        "solution_trajectory": challenge_info.get("solution_trajectory", []),
        "metadata": {
            "easy_prompt": challenge_info["prompts"].get("easy", ""),
            "hard_prompt": challenge_info["prompts"].get("hard", ""),
            "subtasks": challenge_info.get("subtasks", []),
        },
    }


def process_challenges(base_dir: Path, output_dir: Path, combined_name: Optional[str] = None) -> List[Dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)

    all_challenges: List[Dict[str, Any]] = []
    challenge_dirs = _discover_challenge_dirs(base_dir)
    if not challenge_dirs:
        raise FileNotFoundError(f"No pbctf challenge folders found under {base_dir}")

    for challenge_dir in challenge_dirs:
        print(f"Processing: {challenge_dir.name}...", end=" ")
        try:
            challenge_info = _extract_challenge_info(challenge_dir, output_dir)
            rag_entry = format_for_rag(challenge_info)
            all_challenges.append(rag_entry)

            individual_output = output_dir / f"{_safe_filename(challenge_dir.name)}.json"
            with open(individual_output, "w", encoding="utf-8") as handle:
                json.dump(rag_entry, handle, indent=2)

            print(f"✓ ({len(rag_entry.get('solution_trajectory', []))} trajectory items)")
        except Exception as exc:
            print(f"✗ Error: {exc}")

    print(f"\n✓ Successfully processed {len(all_challenges)} challenges")
    print(f"✓ Individual files saved to: {output_dir}")

    if combined_name:
        combined_output = output_dir / combined_name
        with open(combined_output, "w", encoding="utf-8") as handle:
            json.dump(all_challenges, handle, indent=2)
        print(f"✓ Combined database: {combined_output}")

    return all_challenges


def main() -> None:
    parser = argparse.ArgumentParser(description="Format pbCTF 2020 challenge folders into RAG JSON.")
    parser.add_argument(
        "base_dir",
        nargs="?",
        default="/Users/yonitrach/Developer/ToroidBot/dataset/pbctf-2020-challs",
        help="Base directory containing category folders for pbCTF 2020 challenges.",
    )
    parser.add_argument(
        "--output-dir",
        default="/Users/yonitrach/Developer/ToroidBot/dataset/formated_rag_data",
        help="Directory to write per-challenge JSON output.",
    )
    parser.add_argument(
        "--combine-name",
        default="pbctf_2020_challs.json",
        help="Optional combined JSON file name to write alongside per-challenge files.",
    )
    args = parser.parse_args()

    challenges = process_challenges(Path(args.base_dir), Path(args.output_dir), args.combine_name)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for challenge in challenges:
        print(f"\n{challenge['task_name']}")
        print(f"   Category: {challenge['category']}")
        print(f"   Difficulty: {challenge['difficulty']}")
        print(f"   Description: {challenge['description'][:80]}...")
        print(f"   Trajectory steps: {len(challenge['solution_trajectory'])}")
        print(f"   Flag: {challenge.get('flag', 'N/A')[:50]}...")


if __name__ == "__main__":
    main()