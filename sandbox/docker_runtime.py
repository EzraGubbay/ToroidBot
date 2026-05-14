"""Docker sandbox for building, running, and verifying challenges.

Lifecycle (build → start → solve → teardown) used by the Validator to check
that challenges actually work. The solve script is executed inside a sibling
container on a private Docker network — never on the host — so LLM-generated
exploit code cannot reach the developer's machine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from agents.schemas import CTFState, ValidationCheck

# Defense-in-depth: ChallengeManifest.name already constrains LLM output via Pydantic
# regex, but the sandbox re-validates before passing names into `docker` so a future
# code path that constructs a name differently can't smuggle in shell-unsafe chars.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")

SOLVER_IMAGE = "python:3.12-slim"
BUILD_TIMEOUT_S = 180
START_TIMEOUT_S = 30
SOLVE_TIMEOUT_S = 90
TEARDOWN_TIMEOUT_S = 30


def _require(value, label: str) -> None:
    if value is None:
        raise RuntimeError(f"DockerSandbox: {label} must be populated before use")


class DockerSandbox:
    """Manages a Docker container + network for challenge verification.

    Use as a context manager so teardown always runs:

        with DockerSandbox(state) as sandbox:
            checks, flag_found, output = sandbox.verify()
    """

    def __init__(self, state: CTFState, work_dir: Path | None = None):
        _require(state.manifest, "state.manifest")
        _require(state.code, "state.code")
        _require(state.infra, "state.infra")

        name = state.manifest.name  # type: ignore[union-attr]
        if not _NAME_RE.match(name):
            raise RuntimeError(
                f"DockerSandbox: manifest.name {name!r} is not a safe Docker identifier"
            )

        self.state = state
        self._owns_work_dir = work_dir is None
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="ctf-sandbox-"))
        self.container_name = f"ctf-{name}"
        self.image_tag = f"ctf-poc/{name}:latest"
        self.network_name = f"ctf-net-{name}"

    # ── context management ──────────────────────────────────────────────────

    def __enter__(self) -> DockerSandbox:
        return self

    def __exit__(self, *_exc) -> None:
        self.teardown()

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def available() -> bool:
        """Return True iff a usable `docker` binary is on PATH and the daemon responds."""
        if shutil.which("docker") is None:
            return False
        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def write_files(self) -> None:
        """Write challenge files and Dockerfile to the working directory."""
        _require(self.state.code, "state.code")
        _require(self.state.infra, "state.infra")

        for filename, content in self.state.code.files.items():  # type: ignore[union-attr]
            path = self.work_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        (self.work_dir / "Dockerfile").write_text(
            self.state.infra.dockerfile, encoding="utf-8"  # type: ignore[union-attr]
        )

    # ── lifecycle steps ─────────────────────────────────────────────────────

    def build(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "build", "-t", self.image_tag, "."],
            cwd=self.work_dir,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
        )

    def create_network(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "network", "create", "--internal", self.network_name],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def start(self) -> subprocess.CompletedProcess:
        """Start the challenge container attached to the sandbox network."""
        _require(self.state.infra, "state.infra")
        cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "--network", self.network_name,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
        ]
        for port in self.state.infra.exposed_ports:  # type: ignore[union-attr]
            cmd.extend(["-p", f"{port}:{port}"])
        cmd.append(self.image_tag)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=START_TIMEOUT_S)

    def run_solver(self) -> subprocess.CompletedProcess:
        """Run the solve script in a sibling container on the sandbox network.

        The script never executes on the host. The solver container is ephemeral,
        read-only, drops all capabilities, and reaches the challenge via the
        Docker-internal hostname (set as $TARGET_HOST).
        """
        _require(self.state.solver, "state.solver")
        solver = self.state.solver  # type: ignore[assignment]

        ext = {"python": ".py", "bash": ".sh"}.get(solver.solve_language, ".py")
        solve_path = self.work_dir / f"solve{ext}"
        solve_path.write_text(solver.solve_script, encoding="utf-8")

        deps = " ".join(_quote(d) for d in solver.dependencies)
        if solver.solve_language == "bash":
            inner = "bash /solve.sh"
        else:
            install = f"pip install --quiet --no-cache-dir {deps} && " if deps else ""
            inner = f"{install}python /solve.py"

        return subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", self.network_name,
                "-e", f"TARGET_HOST={self.container_name}",
                "--read-only",
                "--tmpfs", "/tmp",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "-v", f"{solve_path}:/solve{ext}:ro",
                SOLVER_IMAGE,
                "sh", "-c", inner,
            ],
            capture_output=True,
            text=True,
            timeout=SOLVE_TIMEOUT_S,
        )

    def teardown(self) -> None:
        """Best-effort cleanup. Safe to call multiple times."""
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            capture_output=True, timeout=TEARDOWN_TIMEOUT_S,
        )
        subprocess.run(
            ["docker", "network", "rm", self.network_name],
            capture_output=True, timeout=TEARDOWN_TIMEOUT_S,
        )
        if self._owns_work_dir:
            shutil.rmtree(self.work_dir, ignore_errors=True)

    # ── orchestration ──────────────────────────────────────────────────────

    def verify(self) -> tuple[list[ValidationCheck], bool, str]:
        """Build → start → solve → check flag.

        Returns:
            (checks, flag_captured, raw_output) — `checks` is the per-step result list,
            `flag_captured` is True only if `expected_flag` appears in solver stdout.
        """
        _require(self.state.solver, "state.solver")
        checks: list[ValidationCheck] = []

        self.write_files()

        build = self.build()
        checks.append(_check_from_proc("docker_build", build))
        if build.returncode != 0:
            return checks, False, build.stderr

        net = self.create_network()
        checks.append(_check_from_proc("docker_network_create", net))
        if net.returncode != 0:
            return checks, False, net.stderr

        start = self.start()
        checks.append(_check_from_proc("container_start", start))
        if start.returncode != 0:
            return checks, False, start.stderr

        try:
            solver_proc = self.run_solver()
        except subprocess.TimeoutExpired as e:
            checks.append(ValidationCheck(
                check="solver_run", passed=False,
                detail=f"Solver timed out after {SOLVE_TIMEOUT_S}s",
            ))
            return checks, False, str(e)

        output = (solver_proc.stdout or "") + (solver_proc.stderr or "")
        checks.append(ValidationCheck(
            check="solver_run", passed=solver_proc.returncode == 0,
            detail=f"exit {solver_proc.returncode}",
        ))

        expected = self.state.solver.expected_flag  # type: ignore[union-attr]
        flag_found = expected in output
        checks.append(ValidationCheck(
            check="flag_captured", passed=flag_found,
            detail="flag found in solver stdout" if flag_found
            else f"expected {expected!r} not present in solver output",
        ))
        return checks, flag_found, output


def _check_from_proc(name: str, proc: subprocess.CompletedProcess) -> ValidationCheck:
    return ValidationCheck(
        check=name,
        passed=proc.returncode == 0,
        detail=(proc.stderr or "").strip()[:500] if proc.returncode != 0 else "ok",
    )


def _quote(s: str) -> str:
    """Minimal shlex-style single-quoting for pip arg-list interpolation."""
    if not s or any(c in s for c in " \t\n\"'\\$`"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s
