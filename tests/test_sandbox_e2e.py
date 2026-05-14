"""End-to-end sandbox verification against real Docker.

Skipped unless the Docker daemon is reachable. Exercises the full lifecycle:
image build, --internal network, sibling-container solver, TARGET_HOST
resolution, flag capture, teardown.

Slow (~30s with cold image pulls, faster once python:3.12-slim is cached).
"""

from __future__ import annotations

import pytest

from agents.schemas import (
    Category,
    ChallengeCode,
    ChallengeInfra,
    ChallengeManifest,
    ChallengeSolver,
    CTFState,
)
from sandbox.docker_runtime import DockerSandbox

pytestmark = pytest.mark.skipif(
    not DockerSandbox.available(),
    reason="docker daemon not available",
)

EXPECTED_FLAG = "CTF{sandbox-e2e-works}"

SERVER_PY = '''\
import socket
FLAG = "CTF{sandbox-e2e-works}"
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 1337))
s.listen(1)
while True:
    c, _ = s.accept()
    try:
        data = c.recv(1024).strip()
        if data == b"OPEN":
            c.sendall(FLAG.encode() + b"\\n")
        else:
            c.sendall(b"denied\\n")
    finally:
        c.close()
'''

DOCKERFILE = '''\
FROM python:3.12-slim
WORKDIR /app
COPY server.py /app/server.py
EXPOSE 1337
CMD ["python", "/app/server.py"]
'''

SOLVE_PY = '''\
import os, socket, time, sys
host = os.environ.get("TARGET_HOST", "localhost")
for _ in range(20):
    try:
        with socket.create_connection((host, 1337), timeout=2) as s:
            s.sendall(b"OPEN")
            data = s.recv(1024).decode().strip()
            print(f"FLAG: {data}")
            sys.exit(0)
    except (ConnectionRefusedError, socket.timeout, OSError):
        time.sleep(0.5)
print("could not reach server", file=sys.stderr)
sys.exit(1)
'''


@pytest.fixture
def e2e_state() -> CTFState:
    return CTFState(
        user_prompt="synthetic end-to-end sandbox test",
        manifest=ChallengeManifest(
            name="sandbox-e2e",
            category=Category.MISC,
            difficulty=1,
            vulnerability="trivial: server emits flag on magic word",
            language="python",
            services=["tcp socket"],
            tools_required=["socket"],
            flag=EXPECTED_FLAG,
        ),
        code=ChallengeCode(
            files={"server.py": SERVER_PY},
            entry_point="server.py",
            flag_location="hardcoded in server.py for this E2E test",
            intended_vulnerability="server.py:accept() — returns flag on input 'OPEN'",
        ),
        infra=ChallengeInfra(
            dockerfile=DOCKERFILE,
            exposed_ports=[1337],
            startup_command="python /app/server.py",
        ),
        solver=ChallengeSolver(
            solve_script=SOLVE_PY,
            dependencies=[],
            expected_flag=EXPECTED_FLAG,
            solve_steps=["connect to TARGET_HOST:1337", "send OPEN", "read flag"],
        ),
    )


def test_docker_sandbox_full_lifecycle(e2e_state):
    """Real Docker: build → run → solve in a sibling container → flag captured → teardown."""
    with DockerSandbox(e2e_state) as sandbox:
        checks, flag_captured, output = sandbox.verify()

    failed = [f"{c.check}: {c.detail}" for c in checks if not c.passed]
    assert not failed, f"Sandbox checks failed: {failed}\nSolver output:\n{output}"
    assert flag_captured, f"Expected flag {EXPECTED_FLAG!r} not found in:\n{output}"
    assert EXPECTED_FLAG in output
