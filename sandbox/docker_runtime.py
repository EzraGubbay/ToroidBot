"""Docker sandbox for building, running, and verifying challenges.

Lifecycle (build → start → solve → teardown) used by the Validator to check
that challenges actually work. The solve script is executed inside a sibling
container on a private Docker network — never on the host — so LLM-generated
exploit code cannot reach the developer's machine.

## Schema-driven correctness

Rather than trusting LLM agents to produce mutually consistent artifacts, the
sandbox derives three critical values directly from Pydantic schema fields:

  code.python_packages  → requirements.txt   (never inferred from imports)
  infra.startup_command → Dockerfile CMD      (always overrides whatever DevOps wrote)
  infra.exposed_ports   → readiness poll host (socket connect before running solver)

This means inter-agent inconsistencies (wrong filename in CMD, missing
requirements.txt, etc.) are corrected at sandbox time rather than surfacing as
cryptic Docker build/runtime errors.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agents.schemas import CTFState, ValidationCheck

log = logging.getLogger(__name__)

# Defense-in-depth: ChallengeManifest.name already constrains LLM output via Pydantic
# regex, but the sandbox re-validates before passing names into `docker` so a future
# code path that constructs a name differently can't smuggle in shell-unsafe chars.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")

SOLVER_IMAGE = "python:3.12-slim"
BUILD_TIMEOUT_S = 180
START_TIMEOUT_S = 30
SOLVE_TIMEOUT_S = 90
TEARDOWN_TIMEOUT_S = 30
STARTUP_GRACE_S = 3  # seconds to wait after container start before checking health


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
        """Write challenge files and a corrected Dockerfile to the working directory.

        Three schema-derived fixups are applied unconditionally:
        1. requirements.txt is generated from code.python_packages (not inferred).
        2. The Dockerfile CMD is replaced with infra.startup_command verbatim.
        3. Any COPY targets that are still missing after (1) are logged as warnings.
        """
        _require(self.state.code, "state.code")
        _require(self.state.infra, "state.infra")
        code = self.state.code  # type: ignore[assignment]
        infra = self.state.infra  # type: ignore[assignment]

        for filename, content in code.files.items():
            path = self.work_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        # 1. requirements.txt — authoritative source is code.python_packages.
        if code.python_packages or "requirements.txt" in infra.dockerfile:
            req_content = "\n".join(code.python_packages) + "\n" if code.python_packages else ""
            (self.work_dir / "requirements.txt").write_text(req_content, encoding="utf-8")

        # 2. Dockerfile — strip CMD/ENTRYPOINT/USER lines and inject startup_command.
        #    CMD/ENTRYPOINT: corrects DevOps/Developer filename mismatches.
        #    USER: DevOps sometimes emits `USER ctf` without `RUN useradd`, which builds
        #    successfully but fails at container-start time with "no matching entries in
        #    passwd file". Running as root in the sandbox is safe and avoids this class
        #    of error; real deployments can enforce non-root separately.
        clean_lines = [
            line for line in infra.dockerfile.splitlines()
            if not line.strip().upper().startswith(("CMD", "ENTRYPOINT", "USER"))
        ]
        clean_lines.append(f"CMD {infra.startup_command}")
        (self.work_dir / "Dockerfile").write_text("\n".join(clean_lines) + "\n", encoding="utf-8")

        # 3. Warn about any remaining missing COPY targets.
        self._check_dockerfile_copies()

    def _check_dockerfile_copies(self) -> None:
        """Log warnings for any COPY sources that don't exist in the work dir."""
        dockerfile = self.work_dir / "Dockerfile"
        if not dockerfile.exists():
            return
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if not parts or parts[0].upper() != "COPY":
                continue
            for src in parts[1:-1]:  # all but dest
                if src.startswith("--"):
                    continue
                if not (self.work_dir / src).exists():
                    log.warning(
                        "Dockerfile COPY references missing file %r — build may fail.", src
                    )

    def _container_is_running(self) -> bool:
        """Return True if the challenge container is still running (not crashed/exited)."""
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", self.container_name],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _install_solver_deps(self, deps: list[str]) -> subprocess.CompletedProcess | None:
        """Pre-install Python packages into a host directory with internet access.

        The --internal sandbox network blocks PyPI, so we install first (no network
        restriction) then mount the result read-only into the solver container.
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

    # ── lifecycle steps ─────────────────────────────────────────────────────

    def build(self) -> subprocess.CompletedProcess:
        """Build the challenge image, injecting the flag as a build arg.

        Using --build-arg keeps the flag out of the Dockerfile source (which is
        player-readable) while still baking it into /flag.txt at build time.
        """
        _require(self.state.manifest, "state.manifest")
        flag = self.state.manifest.flag  # type: ignore[union-attr]
        return subprocess.run(
            ["docker", "build", "-t", self.image_tag, "--build-arg", f"FLAG={flag}", "."],
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
        """Start the challenge container attached to the sandbox network.

        No host port publishing (-p) — the solver reaches the challenge via the
        Docker-internal hostname, so publishing to the host is unnecessary and
        risks conflicts with services already bound on the host (e.g. AirPlay on 5000).
        """
        _require(self.state.infra, "state.infra")
        cmd = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "--network", self.network_name,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            self.image_tag,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=START_TIMEOUT_S)

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

        # Pass TARGET_PORT from infra.exposed_ports so the solver knows which port
        # to connect to regardless of what port the app hardcodes internally.
        infra = self.state.infra  # type: ignore[assignment]
        target_port = str(infra.exposed_ports[0]) if infra and infra.exposed_ports else "1337"

        if solver.solve_language == "bash":
            return subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--network", self.network_name,
                    "-e", f"TARGET_HOST={self.container_name}",
                    "-e", f"TARGET_PORT={target_port}",
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

        # Python: pre-install deps with internet access, then solve air-gapped.
        install = self._install_solver_deps(solver.dependencies)
        if install is not None and install.returncode != 0:
            return install

        deps_dir = self.work_dir / "solver-deps"
        cmd = [
            "docker", "run", "--rm",
            "--network", self.network_name,
            "-e", f"TARGET_HOST={self.container_name}",
            "-e", f"TARGET_PORT={target_port}",
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
        """Build → start → wait → solve → check flag.

        Returns:
            (checks, flag_captured, raw_output) — `checks` is the per-step result list,
            `flag_captured` is True only if `expected_flag` appears in solver stdout.
        """
        _require(self.state.solver, "state.solver")
        checks: list[ValidationCheck] = []

        self.write_files()
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

        # Give the server a moment to start, then verify it hasn't crashed.
        # The solver script is responsible for its own connection retry loop.
        # We poll from inside the Docker network (via inspect), not the host port,
        # because --internal networks don't reliably expose ports on macOS Docker Desktop.
        time.sleep(STARTUP_GRACE_S)
        if not self._container_is_running():
            container_logs = subprocess.run(
                ["docker", "logs", self.container_name],
                capture_output=True, text=True, timeout=10,
            )
            output = container_logs.stdout + container_logs.stderr
            print(f"[sandbox] container crashed at startup:\n{output}", file=sys.stderr, flush=True)
            checks.append(ValidationCheck(
                check="server_ready", passed=False,
                detail="container exited before solver could run",
            ))
            return checks, False, output
        print("[sandbox] container running", file=sys.stderr, flush=True)

        print(f"[sandbox] solve script:\n{self.state.solver.solve_script}", file=sys.stderr, flush=True)  # type: ignore[union-attr]

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
            print(
                f"[sandbox] solver exit={solver_proc.returncode}\n"
                f"[sandbox] stdout={solver_proc.stdout!r}\n"
                f"[sandbox] stderr={solver_proc.stderr!r}",
                file=sys.stderr,
                flush=True,
            )
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
