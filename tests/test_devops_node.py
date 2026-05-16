"""DevOps prompt construction should carry retry context between attempts."""

from __future__ import annotations

from agents.schemas import ChallengeInfra, RetryTarget, ValidationResult
from graph.nodes.devops_node import _build_devops_prompt


def test_build_devops_prompt_includes_retry_feedback(state):
    state.infra = ChallengeInfra(
        dockerfile="FROM ubuntu:24.04\nRUN apt-get update\n",
        exposed_ports=[1337],
        startup_command="./challenge",
    )
    state.validation = ValidationResult(
        passed=False,
        flag_captured=False,
        errors=["Dockerfile missing make"],
        retry_instructions="Add make to apt-get install and keep CMD in JSON form.",
        retry_target=RetryTarget.DEVOPS,
    )

    prompt = _build_devops_prompt(state)

    assert "PREVIOUS ATTEMPT FAILED" in prompt
    assert "Dockerfile missing make" in prompt
    assert "Add make to apt-get install" in prompt
    assert "Previous infrastructure output" in prompt
    assert "FROM ubuntu:24.04" in prompt
