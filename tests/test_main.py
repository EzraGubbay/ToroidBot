"""CLI integration: --config loading and override precedence."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agents.schemas import RetryTarget, ValidationCheck, ValidationResult
from orchestrator import main as cli


@pytest.fixture
def yaml_config(tmp_path):
    path = tmp_path / "event.yaml"
    path.write_text(
        "name: TestEvent\n"
        "flag_regex: ^CTF\\{[a-z0-9_-]{8,}\\}$\n"
        "tone: noir\n"
        "default_model: config:default\n"
        "max_retries: 5\n"
        "use_sandbox: false\n",
        encoding="utf-8",
    )
    return path


def test_build_state_no_config():
    args = cli.parse_args_from(["a prompt"])
    state = cli.build_state(args)
    assert state.event is None
    assert state.user_prompt == "a prompt"
    assert state.max_retries == 3  # built-in default
    assert state.use_sandbox is True
    assert not state.has_cli_model_override


def test_build_state_with_config(yaml_config):
    args = cli.parse_args_from(["prompt", "--config", str(yaml_config)])
    state = cli.build_state(args)
    assert state.event is not None
    assert state.event.name == "TestEvent"
    # Event values flow to state where appropriate
    assert state.max_retries == 5
    assert state.use_sandbox is False
    # CLI did NOT pass --model, so no override set
    assert not state.has_cli_model_override


def test_build_state_cli_model_overrides_config(yaml_config):
    args = cli.parse_args_from([
        "prompt", "--config", str(yaml_config), "--model", "cli:override",
    ])
    state = cli.build_state(args)
    assert state.has_cli_model_override
    assert state.model_for("architect") == "cli:override"


def test_build_state_cli_no_sandbox_overrides_config(yaml_config):
    args = cli.parse_args_from([
        "prompt", "--config", str(yaml_config), "--no-sandbox",
    ])
    state = cli.build_state(args)
    # Config says use_sandbox=false; CLI --no-sandbox also says false.
    assert state.use_sandbox is False


def test_build_state_cli_max_retries_overrides_config(yaml_config):
    args = cli.parse_args_from([
        "prompt", "--config", str(yaml_config), "--max-retries", "9",
    ])
    state = cli.build_state(args)
    assert state.max_retries == 9


def test_build_state_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        args = cli.parse_args_from(["prompt", "--config", str(tmp_path / "nope.yaml")])
        cli.build_state(args)


def test_validation_failure_summary_includes_full_feedback(capsys):
    state = cli.CTFState(user_prompt="prompt")
    state.validation = ValidationResult(
        passed=False,
        flag_captured=False,
        checks=[
            ValidationCheck(check="sandbox_available", passed=False, detail="Docker unavailable"),
            ValidationCheck(check="flag_not_in_source", passed=True, detail="ok"),
        ],
        errors=["Sandbox did not capture the expected flag"],
        retry_instructions="Fix Docker first.",
        retry_target=RetryTarget.DEVELOPER,
    )
    state.failed_solver_scripts = ["print('bad')"]

    cli._print_validation_failure_summary(state)

    captured = capsys.readouterr().err
    assert "Validation summary:" in captured
    assert "retry_target: developer" in captured
    assert "failing checks:" in captured
    assert "sandbox_available: Docker unavailable" in captured
    assert "retry instructions:" in captured
    assert "Fix Docker first." in captured
    assert "failed solver scripts retained: 1" in captured
