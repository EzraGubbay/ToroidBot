"""Docker sandbox for building, running, and verifying challenges.

Lifecycle (build → start → solve → teardown) used by the Validator to check
that challenges actually work. The solve script is executed inside a sibling
container on a private Docker network — never on the host — so LLM-generated
exploit code cannot reach the developer's machine.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

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

    def _check_dockerfile_copies(self) -> None:
        """Warn when the Dockerfile COPYs a file that write_files() didn't create.

        This catches Developer/DevOps mismatches (e.g. COPY requirements.txt but
        no requirements.txt in code.files) before Docker produces a cryptic error.
        """
        dockerfile = self.work_dir / "Dockerfile"
        if not dockerfile.exists():
            return
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if not parts or parts[0].upper() != "COPY":
                continue
            # COPY src [src...] dest — all but the last token are sources.
            sources = parts[1:-1]
            for src in sources:
                if src.startswith("--"):  # skip --from=... flags
                    continue
                src_path = self.work_dir / src
                if not src_path.exists():
                    log.warning(
                        "Dockerfile COPY references missing file %r — build will fail. "
                        "The Developer agent should include this file in code.files.",
                        src,
                    )

    def _install_solver_deps(self, deps: list[str]) -> subprocess.CompletedProcess | None:
        """Pre-install Python packages into a host directory with internet access.

        The --internal sandbox network blocks PyPI, so we install first (no network
        restriction) then mount the result read-only into the solver container.
        Returns the CompletedProcess if installation was attempted, None if skipped.
        """
        if not deps:
            return None
        deps_dir = self.work_dir / "solver-deps"
        deps_dir.mkdir(exist_ok=True)
        return subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{deps_dir}:/deps",
                SOLVER_IMAGE,
                "pip", "install", "--quiet", "--no-cache-dir", "-t", "/deps",
                *deps,
            ],
            capture_output=True,
            text=True,
            timeout=SOLVE_TIMEOUT_S,
        )

    def run_solver(self) -> subprocess.CompletedProcess:
        """Run the solve script in a sibling container on the sandbox network.

        The script never executes on the host. The solver container is ephemeral,
        read-only, drops all capabilities, and reaches the challenge via the
        Docker-internal hostname (set as $TARGET_HOST).

        Python dependencies are pre-installed with internet access into a bind-mount
        so that the actual solve step runs fully air-gapped on the --internal network.
        """
        _require(self.state.solver, "state.solver")
        solver = self.state.solver  # type: ignore[assignment]

        ext = {"python": ".py", "bash": ".sh"}.get(solver.solve_language, ".py")
        solve_path = self.work_dir / f"solve{ext}"
        solve_path.write_text(solver.solve_script, encoding="utf-8")

        if solver.solve_language == "bash":
            return subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--network", self.network_name,
                    "-e", f"TARGET_HOST={self.container_name}",
                    "--read-only",
                    "--tmpfs", "/tmp",
                    "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges",
                    "-v", f"{solve_path}:/solve.sh:ro",
                    SOLVER_IMAGE,
                    "bash", "/solve.sh",
                ],
                capture_output=True,
                text=True,
                timeout=SOLVE_TIMEOUT_S,
            )

        # Python path: pre-install deps with internet, then solve air-gapped.
        install = self._install_solver_deps(solver.dependencies)
        if install is not None and install.returncode != 0:
            return install  # surface pip failure as the solver result

        deps_dir = self.work_dir / "solver-deps"
        cmd = [
            "docker", "run", "--rm",
            "--network", self.network_name,
            "-e", f"TARGET_HOST={self.container_name}",
            "--read-only",
            "--tmpfs", "/tmp",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-v", f"{solve_path}:/solve.py:ro",
        ]
        if deps_dir.exists():
            cmd += ["-v", f"{deps_dir}:/deps:ro", "-e", "PYTHONPATH=/deps"]
        cmd += [SOLVER_IMAGE, "python", "/solve.py"]

        return subprocess.run(cmd, capture_output=True, text=True, timeout=SOLVE_TIMEOUT_S)

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

        import sys

        self.write_files()
        self._check_dockerfile_copies()
        print(f"[sandbox] work_dir={self.work_dir}  image={self.image_tag}", file=sys.stderr, flush=True)

        build = self.build()
        print(f"[sandbox] docker build exit={build.returncode}", file=sys.stderr, flush=True)
        if build.returncode != 0:
            print(f"[sandbox] build stderr:\n{build.stderr[:1000]}", file=sys.stderr, flush=True)
        checks.append(_check_from_proc("docker_build", build))
        if build.returncode != 0:
            return checks, False, build.stderr

        net = self.create_network()
        print(f"[sandbox] network create exit={net.returncode}", file=sys.stderr, flush=True)
        checks.append(_check_from_proc("docker_network_create", net))
        if net.returncode != 0:
            return checks, False, net.stderr

        start = self.start()
        print(f"[sandbox] container start exit={start.returncode}", file=sys.stderr, flush=True)
        if start.returncode != 0:
            print(f"[sandbox] start stderr:\n{start.stderr[:500]}", file=sys.stderr, flush=True)
        checks.append(_check_from_proc("container_start", start))
        if start.returncode != 0:
            return checks, False, start.stderr

        # Give the server time to bind before the solver connects.
        print(f"[sandbox] solve script:\n{self.state.solver.solve_script}", file=sys.stderr, flush=True)  # type: ignore[union-attr]
        time.sleep(2)

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
        if not flag_found:
            import sys
            print(
                f"[sandbox] solver exit={solver_proc.returncode}\n"
                f"[sandbox] stdout={solver_proc.stdout!r}\n"
                f"[sandbox] stderr={solver_proc.stderr!r}",
                file=sys.stderr,
                flush=True,
            )
            # Also dump container logs to help diagnose server-side issues.
            container_logs = subprocess.run(
                ["docker", "logs", self.container_name],
                capture_output=True, text=True, timeout=10,
            )
            print(
                f"[sandbox] container logs:\n{container_logs.stdout}{container_logs.stderr}",
                file=sys.stderr,
                flush=True,
            )
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
