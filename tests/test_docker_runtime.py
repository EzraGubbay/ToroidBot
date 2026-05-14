"""Docker sandbox: name validation, availability probe, _quote helper.

Sandbox lifecycle methods that actually shell out to docker are not exercised here —
they require Docker on the test runner. These tests cover the pure logic only.
"""

from __future__ import annotations

import pytest

from agents.schemas import CTFState
from sandbox.docker_runtime import DockerSandbox, _quote


def test_sandbox_init_rejects_unsafe_manifest_name(state):
    # Bypass Pydantic regex by mutating after construction to force the sandbox-level guard.
    state.manifest.__dict__["name"] = "Bad Name; rm -rf /"
    with pytest.raises(RuntimeError) as exc:
        DockerSandbox(state)
    assert "safe Docker identifier" in str(exc.value)


def test_sandbox_init_raises_on_missing_outputs():
    with pytest.raises(RuntimeError):
        DockerSandbox(CTFState(user_prompt="x"))


def test_sandbox_uses_safe_container_and_image_names(state):
    s = DockerSandbox(state)
    assert s.container_name == "ctf-sample-web-1"
    assert s.image_tag == "ctf-poc/sample-web-1:latest"
    assert s.network_name == "ctf-net-sample-web-1"


def test_quote_passes_simple_args_unchanged():
    assert _quote("requests") == "requests"
    assert _quote("pwntools==4.10") == "pwntools==4.10"


def test_quote_quotes_args_with_metacharacters():
    out = _quote("foo; rm -rf /")
    assert out.startswith("'") and out.endswith("'")
    # Inner single quotes survive intact via the '\'' escape pattern.
    assert _quote("don't") == "'don'\\''t'"


def test_available_returns_bool():
    # Just verify it returns a bool without raising on whatever environment the test runs in.
    assert isinstance(DockerSandbox.available(), bool)
