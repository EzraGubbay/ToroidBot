"""EventConfig schema, enums, and validators."""

from __future__ import annotations

import json
import textwrap

import pytest
from pydantic import ValidationError

from agents.event_config import (
    Audience,
    EventConfig,
    PerAgentModels,
    Tone,
    load_event_config,
    slugify_event_name,
)
from agents.schemas import Category


def _base() -> dict:
    return {
        "name": "MegaCTF 2026",
        "flag_regex": r"^CTF\{[a-zA-Z0-9_-]{8,}\}$",
    }


def test_required_fields_loaded():
    cfg = EventConfig(**_base())
    assert cfg.name == "MegaCTF 2026"
    assert cfg.flag_regex == r"^CTF\{[a-zA-Z0-9_-]{8,}\}$"


def test_missing_name_raises():
    with pytest.raises(ValidationError):
        EventConfig(flag_regex=r"^CTF\{[a-z]{8,}\}$")


def test_missing_flag_regex_raises():
    with pytest.raises(ValidationError):
        EventConfig(name="x")


def test_tone_defaults_to_informal():
    cfg = EventConfig(**_base())
    assert cfg.tone == Tone.INFORMAL


def test_theme_defaults_to_none():
    cfg = EventConfig(**_base())
    assert cfg.theme is None


def test_audience_defaults_to_mixed():
    cfg = EventConfig(**_base())
    assert cfg.audience == Audience.MIXED


def test_per_agent_models_defaults_all_none():
    cfg = EventConfig(**_base())
    assert cfg.models.architect is None
    assert cfg.models.storyteller is None
    assert cfg.models.developer is None
    assert cfg.models.devops is None
    assert cfg.models.solver is None
    assert cfg.models.validator is None


def test_per_agent_models_accepts_overrides():
    cfg = EventConfig(
        **_base(),
        models=PerAgentModels(architect="openai:gpt-4.1"),
    )
    assert cfg.models.architect == "openai:gpt-4.1"


def test_per_agent_models_rejects_unknown_agent():
    with pytest.raises(ValidationError):
        EventConfig(**_base(), models={"unknown_agent": "openai:gpt-4.1"})


def test_invalid_regex_raises():
    with pytest.raises(ValidationError) as exc:
        EventConfig(name="x", flag_regex=r"[unclosed")
    assert "not a valid regex" in str(exc.value)


@pytest.mark.parametrize("regex", [
    r"^.*$",                          # matches everything (incl. empty)
    r"^CTF\{.\}$",                    # exactly one char inside
    r".",                             # matches single char
    r"",                              # empty regex matches empty
])
def test_regex_must_require_min_length(regex):
    with pytest.raises(ValidationError) as exc:
        EventConfig(name="x", flag_regex=regex)
    assert "minimum length" in str(exc.value)


def test_forbidden_categories_must_be_known():
    cfg = EventConfig(**_base(), forbidden_categories=[Category.PWN])
    assert cfg.forbidden_categories == [Category.PWN]

    with pytest.raises(ValidationError):
        EventConfig(**_base(), forbidden_categories=["nonsense"])


def test_default_model_falls_back_to_builtin():
    cfg = EventConfig(**_base())
    assert cfg.default_model == "google-gla:gemini-2.5-flash"


def test_use_sandbox_defaults_true():
    cfg = EventConfig(**_base())
    assert cfg.use_sandbox is True


def test_max_retries_defaults_three():
    cfg = EventConfig(**_base())
    assert cfg.max_retries == 3


def test_rag_top_k_defaults_three():
    cfg = EventConfig(**_base())
    assert cfg.rag_top_k == 3


def test_load_event_config_yaml(tmp_path):
    path = tmp_path / "event.yaml"
    path.write_text(textwrap.dedent("""\
        name: MegaCTF 2026
        flag_regex: ^CTF\\{[a-zA-Z0-9_-]{8,}\\}$
        tone: noir
        theme: corporate espionage
    """), encoding="utf-8")
    cfg = load_event_config(path)
    assert cfg.name == "MegaCTF 2026"
    assert cfg.tone.value == "noir"
    assert cfg.theme == "corporate espionage"


def test_load_event_config_json(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({
        "name": "Quickstart CTF",
        "flag_regex": r"^CTF\{[a-zA-Z0-9_-]{8,}\}$",
    }), encoding="utf-8")
    cfg = load_event_config(path)
    assert cfg.name == "Quickstart CTF"
    assert cfg.tone.value == "informal"  # default


def test_load_event_config_unknown_extension_raises(tmp_path):
    path = tmp_path / "event.toml"
    path.write_text("name = 'x'\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_event_config(path)
    assert "extension" in str(exc.value).lower()


def test_load_event_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_event_config(tmp_path / "does-not-exist.yaml")


def test_load_event_config_invalid_field_raises(tmp_path):
    path = tmp_path / "event.yaml"
    path.write_text("name: x\nflag_regex: ^CTF\\{.*\\}$\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_event_config(path)


@pytest.mark.parametrize("raw,slug", [
    ("MegaCTF 2026", "megactf-2026"),
    ("Hello World", "hello-world"),
    ("  Spaced  Out  ", "spaced-out"),
    ("Mixed Case 123", "mixed-case-123"),
    ("Path/Traversal..", "path-traversal"),
    ("$(rm -rf)!!", "rm-rf"),
    ("---weird---", "weird"),
])
def test_slugify_event_name(raw, slug):
    assert slugify_event_name(raw) == slug


def test_slugify_caps_length():
    long_name = "a" * 200
    slug = slugify_event_name(long_name)
    assert len(slug) <= 64
    assert slug == "a" * 64


def test_slugify_caps_length_trims_trailing_hyphen():
    # 64th char lands on a hyphen boundary: 63 a's + space + more
    name = "a" * 63 + " bbbbb"
    slug = slugify_event_name(name)
    assert len(slug) <= 64
    assert not slug.endswith("-")


def test_slugify_empty_raises():
    with pytest.raises(ValueError):
        slugify_event_name("!!!")
