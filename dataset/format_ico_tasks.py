#!/usr/bin/env python3
"""
Format ICO task challenge folders from `dataset/ico-tasks/quals` into the RAG JSON schema.

This variant is tailored for challenges that use `challenge.yml` metadata and a mixed
layout of `dist`, `distrib`, `service`, `srv`, `sol`, and `src` folders.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - only used when PyYAML is unavailable
    yaml = None


TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".go",
    ".h",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".lua",
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
    "dockerfile",
    "Makefile",
    "README",
    "README.md",
    "challenge.json",
    "challenge.yaml",
    "challenge.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
}

IGNORED_FILENAMES = {"flag.txt"}
IGNORED_DIR_NAMES = {"__pycache__", ".git", ".venv", "node_modules", "target", "venv"}

INTERESTING_STEMS = {
    "create",
    "decode",
    "decode_file",
    "encode",
    "encode_file",
    "exploit",
    "formal",
    "pure_python_solution",
    "readme",
    "solve",
    "solver",
    "solution",
    "writeup",
    "exp",
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
    if suffix == ".json":
        return "json"
    return "unknown"


def _file_role(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    stem = path.stem.lower()
    if stem in {"solve", "solver", "exp", "exploit", "solution", "writeup"} or "sol" in parts:
        return "solution"
    if path.name in TEXT_FILENAMES:
        return "challenge_source"
    if any(name in parts for name in {"challenge", "src", "dist", "distrib", "service", "srv", "conf", "config"}):
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

    return [
        {
            "role": "solution",
            "action": "description",
            "command": " ".join(line.strip() for line in text.splitlines() if line.strip()),
            "source_file": source_file,
        }
    ]


def _load_challenge_metadata(challenge_dir: Path) -> Dict[str, Any]:
    for metadata_name in ("challenge.yml", "challenge.yaml", "challenge.json"):
        metadata_path = challenge_dir / metadata_name
        if not metadata_path.exists():
            continue
        if metadata_path.suffix.lower() == ".json":
            with open(metadata_path, "r", encoding="utf-8", errors="replace") as handle:
                return json.load(handle)
        if yaml is None:
            raise RuntimeError("PyYAML is required to read challenge.yml files")
        with open(metadata_path, "r", encoding="utf-8", errors="replace") as handle:
            loaded = yaml.safe_load(handle)
            return loaded if isinstance(loaded, dict) else {}
    return {}


def _subtask(
    subtask: str,
    question: str,
    answer: str,
    answer_format: str,
    hints: List[str],
    commands: List[str],
    context: str,
    tools_languages: List[str],
    solution_files: List[str] | None = None,
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


def _build_subtasks(challenge_dir: Path, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = challenge_dir.name
    flag = str((metadata.get("flags") or [metadata.get("flag", "")])[0])

    if name == "crypto-aesgoatedmode":
        return [
            _subtask(
                "Spot the AES-GCM flaw.",
                "What bug in the AES-GCM implementation makes the attack possible?",
                "invalid tag decryption bug",
                "******* **** ******** ******",
                [
                    "Read the explanatory comments in solve.py.",
                    "Focus on what happens when decryption is attempted with a bad tag.",
                ],
                ["cat sol/solve.py"],
                "The solver notes that the decrypt path still returns plaintext even when the authentication tag is invalid.",
                ["python"],
                ["sol/solve.py"],
            ),
            _subtask(
                "Recover the keystream.",
                "What do you need to send to the decrypt endpoint to recover the keystream?",
                "an attacker-controlled ciphertext with the same nonce",
                "** ********* *********** ********* **** **** *****",
                [
                    "The nonce printed by the challenge must be reused.",
                    "A known plaintext lets you derive the stream that was XORed with the flag.",
                ],
                ["python3 sol/solve.py"],
                "The attack feeds a chosen ciphertext into the decrypt path with the original nonce, then XORs the result with the known plaintext to recover the keystream.",
                ["python"],
                ["sol/solve.py"],
            ),
            _subtask(
                "Recover the flag.",
                "What's the flag?",
                flag,
                "ICO{**************************}",
                ["Run the provided solve script after recovering the keystream."],
                ["python3 sol/solve.py"],
                "Decrypt the flag ciphertext with the recovered keystream and print the plaintext flag.",
                ["python"],
                ["sol/solve.py"],
            ),
        ]

    if name == "crypto-funny-little-trial":
        return [
            _subtask(
                "Identify the cryptosystem.",
                "Which public-key system is used in chall.json?",
                "RSA",
                "***",
                [
                    "Inspect dist/chall.json.",
                    "Look for familiar RSA parameters like n, e, c, and a helper value.",
                ],
                ["cat dist/chall.json"],
                "The challenge data is a standard RSA setup with an extra value that can help recover the modulus structure.",
                ["bash"],
            ),
            _subtask(
                "Use the helper value.",
                "Which extra value in chall.json helps reconstruct the modulus structure?",
                "s",
                "*",
                [
                    "The generator script stores one additional value besides n, e, and c.",
                    "That value is defined from p and q and can be used to derive the hidden factors.",
                ],
                ["cat dist/chall.py", "cat dist/chall.json"],
                "The generator computes a helper term from p and q before writing the challenge JSON.",
                ["bash", "python"],
                ["dist/chall.py", "dist/chall.json"],
            ),
            _subtask(
                "Recover the flag.",
                "What's the flag?",
                flag,
                "ICO{***********************}",
                ["Use the recovered factors to decrypt the ciphertext."],
                ["python3 solve.py"],
                "Once the modulus is reconstructed, decrypt c with the RSA private key and read the flag.",
                ["python"],
                ["sol/solve.py"],
            ),
        ]

    if name == "pwn-carpark":
        return [
            _subtask(
                "Find the bounds bug.",
                "What kind of memory bug lets the first carpark write past its boundary?",
                "off-by-one out-of-bounds write",
                "***-**-*** *********** ****",
                [
                    "Read distrib/chall.cpp and look at the slot validation checks.",
                    "The first array allows one index too far.",
                ],
                ["cat distrib/chall.cpp"],
                "The first bounds check allows slot 10, which writes one element past the end of carpark1.",
                ["c++"],
                ["distrib/chall.cpp"],
            ),
            _subtask(
                "Corrupt the adjacent object.",
                "What object next to carpark1 can be corrupted to gain arbitrary read/write?",
                "the carpark2 vector object",
                "*** ********* ** ********** ****",
                [
                    "Look at how Carparks lays out its members in memory.",
                    "The vector lives right after the fixed-size array.",
                ],
                ["cat distrib/chall.cpp"],
                "The stack-allocated struct stores the fixed array before the vector, so a one-past-end write can modify the vector metadata.",
                ["c++"],
                ["distrib/chall.cpp"],
            ),
            _subtask(
                "Recover the flag.",
                "What's the flag?",
                flag,
                "ICO{***************************************}",
                ["Exploit the corrupted vector to read and overwrite the target process state."],
                ["python3 sol/exp.py"],
                "The exploit uses the corrupted vector pointer to build arbitrary read and arbitrary write primitives, then redirects execution.",
                ["c++", "python"],
                ["sol/exp.py"],
            ),
        ]

    if name == "pwn-secret-agent-portal":
        return [
            _subtask(
                "Bypass the login check.",
                "What bug lets you bypass the password check?",
                "stack buffer overflow",
                "***** ****** ********",
                [
                    "Inspect the authentication logic in solve.py.",
                    "The password input is overlong and can be used to smash adjacent state.",
                ],
                ["cat solve.py"],
                "The exploit first sends an overlong password to bypass the portal's intended authentication path.",
                ["python"],
                ["solve.py"],
            ),
            _subtask(
                "Leak libc from the heap.",
                "What base do you recover after reading back a freed chunk?",
                "libc base",
                "**** ****",
                [
                    "The exploit frees a chunk and then reads it back.",
                    "The leak is used to compute the address of libc symbols.",
                ],
                ["python3 solve.py"],
                "A freed chunk is reused as an info leak, revealing a libc pointer that anchors the rest of the exploit.",
                ["python"],
                ["solve.py"],
            ),
            _subtask(
                "Recover the flag.",
                "What's the flag?",
                flag,
                "ICO{*************************************************}",
                ["Use the heap leak and poisoning primitive to redirect control flow."],
                ["python3 solve.py"],
                "The exploit combines a heap leak, tcache poisoning, and a gadget overwrite to reach the flag.",
                ["python"],
                ["solve.py"],
            ),
        ]

    if name == "web-tung-tung-tung-sahur":
        return [
            _subtask(
                "Spot the template injection.",
                "Which form field is evaluated as a Go template and can be used for injection?",
                "Tralala",
                "*******",
                [
                    "Look at the POST handler in solve.py.",
                    "The payload is placed into the Tralala parameter.",
                ],
                ["cat solve.py"],
                "The solve script posts a Go template expression through the Tralala field to reach server-side context objects.",
                ["bash", "go"],
                ["solve.py"],
            ),
            _subtask(
                "Call the flag function.",
                "What callable stored in context returns the flag once the secret is known?",
                "Flag",
                "****",
                [
                    "The template context contains a struct with a secret and a function.",
                    "Look at the `.Context` and `.Value \"tung\"` chain in solve.py.",
                ],
                ["python3 solve.py"],
                "The payload uses template reflection to retrieve the `tung` context object and then call its `Flag` function with the secret.",
                ["go", "python"],
                ["solve.py"],
            ),
            _subtask(
                "Recover the flag.",
                "What's the flag?",
                flag,
                "ICO{**************************************************************}",
                ["Submit the SSTI payload, then read the /sahur page output."],
                ["python3 solve.py"],
                "Once the secret is recovered, the `Flag` function returns the final flag string.",
                ["go", "python"],
                ["solve.py"],
            ),
        ]

    if name == "rev-a-complicated-secret":
        return [
            _subtask(
                "Choose the right analysis tools.",
                "Which dynamic-analysis tools do the provided solve scripts use?",
                "angr and unicorn",
                "**** and *******",
                [
                    "Read the two solve scripts in sol/.",
                    "Both scripts lift instructions and build equations over flag bytes.",
                ],
                ["cat sol/solve_angr_emulation.py", "cat sol/solve_unicorn.py"],
                "The solver uses symbolic execution / emulation to walk through the binary and gather the constraint system.",
                ["python"],
                ["sol/solve_angr_emulation.py", "sol/solve_unicorn.py"],
            ),
            _subtask(
                "Remove the final encoding layer.",
                "What encoding must be removed after solving the equation system?",
                "base64",
                "******",
                [
                    "Look at the end of solve_unicorn.py.",
                    "The recovered byte string is decoded before printing the flag.",
                ],
                ["cat sol/solve_unicorn.py"],
                "After the constraint system is solved, the result is still base64-encoded and must be decoded to reveal the flag.",
                ["python"],
                ["sol/solve_unicorn.py"],
            ),
            _subtask(
                "Recover the flag.",
                "What's the flag?",
                flag,
                "ICO{******************************}",
                ["Run either solver after reconstructing the constraints.", "Decode the resulting base64 text."],
                ["python3 sol/solve_unicorn.py"],
                "The reconstructed bytes decode to the base64-encoded flag.",
                ["python"],
                ["sol/solve_unicorn.py"],
            ),
        ]

    return []


def _gather_text_files(challenge_dir: Path) -> List[Path]:
    candidates: List[Path] = []
    for path in sorted(challenge_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.name in IGNORED_FILENAMES:
            continue
        suffix = path.suffix.lower()
        if path.name in TEXT_FILENAMES or suffix in TEXT_EXTENSIONS or path.stem.lower() in INTERESTING_STEMS:
            candidates.append(path)
    return candidates


def _safe_filename(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "-")
    return "".join(ch for ch in normalized if ch.isalnum() or ch in {"-", "_"}) or "challenge"


def extract_challenge_info(challenge_dir: Path, output_dir: Path) -> Dict[str, Any]:
    """
    Extract relevant information from an ICO task folder.
    Paths are stored relative to output_dir for portability.
    """
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

    difficulty = metadata.get("difficulty", metadata.get("value", ""))
    if difficulty is None:
        difficulty = ""
    difficulty = str(difficulty)

    connection_info = str(metadata.get("connection_info", "")).strip()
    file_list = metadata.get("files", [])
    if isinstance(file_list, list):
        file_list_text = ", ".join(str(item) for item in file_list if str(item).strip())
    else:
        file_list_text = ""

    prompt_prefix = f"You are tasked with a {category.lower() if category else 'CTF'} challenge named {challenge_name}."
    if description:
        prompt_prefix += f" The challenge description is: {description}"
    if connection_info:
        prompt_prefix += f" The service is reachable at {connection_info}."
    if file_list_text:
        prompt_prefix += f" You are given the following files: {file_list_text}."

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
        "subtasks": _build_subtasks(challenge_dir, metadata),
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
        source_path_resolved = source_path.resolve()
        source_path_from_output = str(Path(os.path.relpath(source_path_resolved, output_dir_resolved)))
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
            if source_path.suffix.lower() == ".txt" and "```" not in content and len(content.splitlines()) <= 3:
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

    info["solution_trajectory"] = trajectory
    info["files"] = files
    return info


def format_for_rag(challenge_info: Dict[str, Any]) -> Dict[str, Any]:
    """Format challenge info into the RAG database schema."""
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


def _discover_challenge_dirs(base_dir: Path) -> List[Path]:
    metadata_files = ("challenge.yml", "challenge.yaml", "challenge.json")
    for metadata_name in metadata_files:
        if (base_dir / metadata_name).is_file():
            return [base_dir]

    challenge_dirs: List[Path] = []
    seen = set()
    for metadata_name in metadata_files:
        for path in sorted(base_dir.rglob(metadata_name)):
            parent = path.parent
            if parent in seen:
                continue
            seen.add(parent)
            challenge_dirs.append(parent)
    return challenge_dirs


def process_challenges(base_dir: Path, output_dir: Path, combined_name: str | None = None) -> List[Dict[str, Any]]:
    """Generate per-challenge JSON files and an optional combined database file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    all_challenges: List[Dict[str, Any]] = []
    challenge_dirs = _discover_challenge_dirs(base_dir)
    if not challenge_dirs:
        raise FileNotFoundError(f"No challenge.yml/challenge.json files found under {base_dir}")

    for challenge_dir in challenge_dirs:
        print(f"Processing: {challenge_dir.name}...", end=" ")
        try:
            challenge_info = extract_challenge_info(challenge_dir, output_dir)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Format ICO task challenge folders into RAG JSON.")
    parser.add_argument(
        "base_dir",
        nargs="?",
        default="/Users/yonitrach/Developer/ToroidBot/dataset/ico-tasks/quals",
        help="Base directory containing challenge folders or a single challenge folder.",
    )
    parser.add_argument(
        "--output-dir",
        default="/Users/yonitrach/Developer/ToroidBot/dataset/formated_rag_data",
        help="Directory to write per-challenge JSON output.",
    )
    parser.add_argument(
        "--combine-name",
        default=None,
        help="Optional combined JSON file name to write alongside per-challenge files.",
    )
    args = parser.parse_args()

    challenges = process_challenges(Path(args.base_dir), Path(args.output_dir), args.combine_name)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for challenge in challenges:
        print(f"\n📌 {challenge['task_name']}")
        print(f"   Category: {challenge['category']}")
        print(f"   Difficulty: {challenge['difficulty']}")
        print(f"   Description: {challenge['description'][:80]}...")
        print(f"   Trajectory steps: {len(challenge['solution_trajectory'])}")
        print(f"   Flag: {challenge.get('flag', 'N/A')[:50]}...")