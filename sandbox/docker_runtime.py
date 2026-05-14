"""Docker sandbox for building, running, and verifying challenges.

This module handles the build → run → solve → teardown lifecycle
used by the Validator to check that challenges actually work.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from agents.schemas import CTFState


class DockerSandbox:
    """Manages a Docker container for challenge verification."""

    def __init__(self, state: CTFState, work_dir: Optional[Path] = None):
        assert state.manifest is not None
        assert state.code is not None
        assert state.infra is not None

        self.state = state
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="ctf-sandbox-"))
        self.container_name = f"ctf-{state.manifest.name}"
        self.image_tag = f"ctf-poc/{state.manifest.name}:latest"

    def write_files(self) -> None:
        """Write challenge files and Dockerfile to the working directory."""
        assert self.state.code is not None
        assert self.state.infra is not None

        for filename, content in self.state.code.files.items():
            path = self.work_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        (self.work_dir / "Dockerfile").write_text(
            self.state.infra.dockerfile, encoding="utf-8"
        )

    def build(self) -> subprocess.CompletedProcess:
        """Build the Docker image."""
        return subprocess.run(
            ["docker", "build", "-t", self.image_tag, "."],
            cwd=self.work_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def start(self) -> subprocess.CompletedProcess:
        """Start the challenge container."""
        assert self.state.infra is not None
        ports = self.state.infra.exposed_ports

        cmd = ["docker", "run", "-d", "--name", self.container_name]
        for port in ports:
            cmd.extend(["-p", f"{port}:{port}"])
        cmd.append(self.image_tag)

        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def run_solver(self) -> subprocess.CompletedProcess:
        """Run the solve script against the running container."""
        assert self.state.solver is not None

        ext = {"python": ".py", "bash": ".sh"}.get(self.state.solver.solve_language, ".py")
        solve_path = self.work_dir / f"solve{ext}"
        solve_path.write_text(self.state.solver.solve_script, encoding="utf-8")

        return subprocess.run(
            ["python", str(solve_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def teardown(self) -> None:
        """Stop and remove the container."""
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            capture_output=True,
            timeout=15,
        )

    def verify(self) -> tuple[bool, str]:
        """Full verification lifecycle: build → start → solve → teardown.

        Returns:
            Tuple of (flag_captured: bool, output: str).
        """
        try:
            self.write_files()

            build = self.build()
            if build.returncode != 0:
                return False, f"Build failed:\n{build.stderr}"

            start = self.start()
            if start.returncode != 0:
                return False, f"Container start failed:\n{start.stderr}"

            result = self.run_solver()
            output = result.stdout + result.stderr

            assert self.state.solver is not None
            flag_found = self.state.solver.expected_flag in output

            return flag_found, output
        finally:
            self.teardown()
