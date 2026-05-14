#!/usr/bin/env python3
"""
Script to format Cyber-Zero challenges from crypto folder into RAG database format.
Takes well-structured challenges and outputs them to formated_rag_data folder.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Any


TEXT_EXTENSIONS = {".md", ".txt", ".sh", ".py", ".sage", ".rs", ".v", ".sv", ".sby", ".toml", ".yml", ".yaml"}
IGNORED_FILENAMES = {"flag.txt"}
IGNORED_DIR_NAMES = {"__pycache__", "node_modules", "target", ".git"}
SOURCE_DIR_NAMES = {"challenge", "src", "htb", "solution", "dist", "conf", "config"}
INTERESTING_STEMS = {
    "writeup",
    "readme",
    "solution",
    "solve",
    "exploit",
    "decode",
    "decode_file",
    "encode",
    "encode_file",
    "create",
    "pure_python_solution",
    "formal",
}


def _read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


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
    return "unknown"


def _file_role(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if "solution" in parts or "solve" in path.stem.lower():
        return "solution"
    if any(name in parts for name in {"challenge", "src", "htb", "dist"}):
        return "challenge_source"
    return "supporting"


def _append_text_step(trajectory: List[Dict[str, str]], text: str, source_file: str) -> None:
    cleaned = text.strip()
    if not cleaned:
        return
    if len(cleaned) <= 10:
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


def _append_code_step(
    trajectory: List[Dict[str, str]],
    code: List[str],
    language: str,
    source_file: str,
) -> None:
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


def _parse_text_file_to_trajectory(text: str, source_file: str) -> List[Dict[str, str]]:
    if "```" in text or "# " in text:
        return _parse_markdown_to_trajectory(text, source_file)

    return [{
        "role": "solution",
        "action": "description",
        "command": " ".join(line.strip() for line in text.splitlines() if line.strip()),
        "source_file": source_file,
    }]


def _gather_solution_sources(challenge_dir: Path) -> List[Path]:
    candidates: List[Path] = []
    for relative_dir in (challenge_dir / "solution", challenge_dir / "metadata" / "solution"):
        if not relative_dir.exists():
            continue
        for path in sorted(relative_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name in IGNORED_FILENAMES:
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            stem = path.stem.lower()
            if path.suffix.lower() == ".md" or stem in INTERESTING_STEMS:
                candidates.append(path)
            elif path.name == "README.md":
                candidates.append(path)
    return candidates


def _gather_source_files(challenge_dir: Path) -> List[Path]:
    candidates: List[Path] = []
    for root_name in SOURCE_DIR_NAMES:
        relative_dir = challenge_dir / root_name
        if not relative_dir.exists():
            continue
        for path in sorted(relative_dir.rglob("*")):
            if not path.is_file():
                continue
            if any(part in IGNORED_DIR_NAMES for part in path.parts):
                continue
            if path.name in IGNORED_FILENAMES:
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            if path.suffix.lower() == ".md" or path.stem.lower() in INTERESTING_STEMS:
                candidates.append(path)
            elif path.name in {"README.md", "Dockerfile", "Makefile", "docker-compose.yml", "docker-compose.yaml"}:
                candidates.append(path)
    return candidates


def extract_challenge_info(challenge_dir: Path, output_dir: Path) -> Dict[str, Any]:
    """
    Extract all relevant information from a challenge directory.
    Paths are stored relative to output_dir for portability.
    """
    challenge_dir_resolved = challenge_dir.resolve()
    output_dir_resolved = output_dir.resolve()
    challenge_base_path_relative = str(Path(os.path.relpath(challenge_dir_resolved, output_dir_resolved)))
    
    info = {
        "task_name": "",
        "description": "",
        "category": "",
        "difficulty": "",
        "flag": "",
        "challenge_base_path": challenge_base_path_relative,
        "solution_trajectory": [],
        "source_files": [],
        "files": [],
        "prompts": {"easy": "", "hard": ""},
        "subtasks": []
    }
    
    # Read challenge.json
    challenge_json = challenge_dir / "challenge.json"
    if challenge_json.exists():
        with open(challenge_json, 'r') as f:
            ch_data = json.load(f)
            info["task_name"] = ch_data.get("name", "")
            info["description"] = ch_data.get("description", "")
            info["category"] = ch_data.get("category", "")
            info["flag"] = ch_data.get("flag", "")
    
    # Read metadata.json for difficulty and prompts
    metadata_json = challenge_dir / "metadata" / "metadata.json"
    if metadata_json.exists():
        with open(metadata_json, 'r') as f:
            meta_data = json.load(f)
            info["difficulty"] = meta_data.get("difficulty", "")
            info["prompts"]["easy"] = meta_data.get("easy_prompt", "")
            info["prompts"]["hard"] = meta_data.get("hard_prompt", "")
            info["subtasks"] = meta_data.get("subtasks", [])
    
    # Read flag from metadata/solution/flag.txt if exists
    flag_file = challenge_dir / "metadata" / "solution" / "flag.txt"
    if flag_file.exists():
        with open(flag_file, 'r', encoding='utf-8', errors='replace') as f:
            info["flag"] = f.read().strip()
    
    # Collect challenge source and solution files so the JSON can be used directly for RAG.
    source_paths = _gather_source_files(challenge_dir)
    solution_paths = _gather_solution_sources(challenge_dir)

    combined_paths: List[Path] = []
    seen_paths = set()
    for path in source_paths + solution_paths:
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
        source_path_resolved = source_path.resolve()
        source_path_from_output = str(Path(os.path.relpath(source_path_resolved, output_dir_resolved)))
        role = _file_role(source_path)
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
        elif source_path.suffix.lower() in {".txt", ".sh", ".py", ".sage", ".rs", ".v", ".sv", ".toml", ".yml", ".yaml"}:
            if source_path.suffix.lower() == ".txt" and "```" not in content and len(content.splitlines()) <= 3:
                trajectory.extend(_parse_text_file_to_trajectory(content, relative_source))
            else:
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


def format_for_rag(challenge_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format challenge info into RAG database format.
    """
    rag_entry = {
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
            "subtasks": challenge_info.get("subtasks", [])
        }
    }
    return rag_entry


def _discover_challenge_dirs(base_dir: Path) -> List[Path]:
    if (base_dir / "challenge.json").is_file():
        return [base_dir]

    challenge_dirs: List[Path] = []
    for path in sorted(base_dir.rglob("challenge.json")):
        challenge_dirs.append(path.parent)
    return challenge_dirs


def _safe_filename(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "-")
    return "".join(ch for ch in normalized if ch.isalnum() or ch in {"-", "_"}) or "challenge"


def process_challenges(base_dir: Path, output_dir: Path, combined_name: str | None = None):
    """
    Main function to process challenge directories and generate RAG JSON files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    all_challenges = []
    
    challenge_dirs = _discover_challenge_dirs(base_dir)
    if not challenge_dirs:
        raise FileNotFoundError(f"No challenge.json files found under {base_dir}")

    for challenge_dir in challenge_dirs:
        
        print(f"Processing: {challenge_dir.name}...", end=" ")
        
        try:
            challenge_info = extract_challenge_info(challenge_dir, output_dir)
            rag_entry = format_for_rag(challenge_info)
            all_challenges.append(rag_entry)
            
            # Also save individual challenge JSON
            individual_output = output_dir / f"{_safe_filename(challenge_dir.name)}.json"
            with open(individual_output, 'w') as f:
                json.dump(rag_entry, f, indent=2)
            
            print(f"✓ ({len(rag_entry.get('solution_trajectory', []))} trajectory items)")
        except Exception as e:
            print(f"✗ Error: {e}")
    
    print(f"\n✓ Successfully processed {len(all_challenges)} challenges")
    print(f"✓ Individual files saved to: {output_dir}")

    if combined_name:
        combined_output = output_dir / combined_name
        with open(combined_output, 'w') as f:
            json.dump(all_challenges, f, indent=2)
        print(f"✓ Combined database: {combined_output}")
    
    return all_challenges


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Format Cyber-Zero challenge folders into RAG JSON.")
    parser.add_argument(
        "base_dir",
        nargs="?",
        default="/Users/yonitrach/Developer/dataCTF/cyber-zero/benchmarks/cybench/GLA/crypto",
        help="Base directory containing challenge folders or a single challenge folder.",
    )
    parser.add_argument(
        "--output-dir",
        default="/Users/yonitrach/Developer/dataCTF/formated_rag_data",
        help="Directory to write per-challenge JSON output.",
    )
    parser.add_argument(
        "--combine-name",
        default=None,
        help="Optional combined JSON file name to write alongside per-challenge files.",
    )
    args = parser.parse_args()

    challenges = process_challenges(Path(args.base_dir), Path(args.output_dir), args.combine_name)
    
    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for ch in challenges:
        print(f"\n📌 {ch['task_name']}")
        print(f"   Category: {ch['category']}")
        print(f"   Difficulty: {ch['difficulty']}")
        print(f"   Description: {ch['description'][:80]}...")
        print(f"   Trajectory steps: {len(ch['solution_trajectory'])}")
        print(f"   Flag: {ch.get('flag', 'N/A')[:50]}...")
