import time

import pytest
from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)


def test_generate_and_run_lifecycle():
    # start a run
    body = {
        "mode": "intent",
        "difficulty": "easy",
        "category": "web",
        "topic": "xss",
        "constraints": {"language": "python"},
    }
    r = client.post("/generate", json=body)
    assert r.status_code == 202
    data = r.json()
    assert "run_id" in data
    run_id = data["run_id"]

    # list runs should include this run
    r = client.get("/runs")
    assert r.status_code == 200
    runs = r.json()
    assert any(item["run_id"] == run_id for item in runs)

    # wait a short while for the background simulation to progress
    time.sleep(1.0)

    # get run detail
    r = client.get(f"/runs/{run_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["summary"]["run_id"] == run_id

    # artifacts endpoint should list artifacts (developer stage creates one)
    r = client.get(f"/runs/{run_id}/artifacts")
    assert r.status_code == 200
    arts = r.json()
    assert isinstance(arts, list)

    # artifact download (placeholder) should succeed for known path
    if arts:
        path = arts[0]["path"]
        r = client.get(f"/runs/{run_id}/artifacts/{path}")
        assert r.status_code == 200


def test_get_nonexistent_run():
    r = client.get("/runs/nonexistent-run")
    assert r.status_code == 404


def test_kb_search_is_not_shadowed_and_requires_auth_for_write(monkeypatch):
    monkeypatch.setenv("TOROIDBOT_ADMIN_KEY", "test-admin-key")

    # import without auth should be rejected
    r = client.post("/kb/import", json={"path": "/tmp/example"})
    assert r.status_code == 403

    r = client.post(
        "/kb/import",
        headers={"X-API-Key": "test-admin-key"},
        json={"path": "/tmp/example"},
    )
    assert r.status_code == 200
    kb_id = r.json()["id"]

    # /kb/search must not be captured by /kb/{kb_id}
    r = client.get("/kb/search", params={"q": "example"})
    assert r.status_code == 200
    assert any(item["id"] == kb_id for item in r.json())


def test_settings_and_presets_are_protected_or_error_correctly(monkeypatch):
    monkeypatch.setenv("TOROIDBOT_ADMIN_KEY", "test-admin-key")

    # settings write requires auth
    r = client.put("/settings", json={"verification_enabled": False})
    assert r.status_code == 403

    r = client.put(
        "/settings",
        headers={"X-API-Key": "test-admin-key"},
        json={"verification_enabled": False},
    )
    assert r.status_code == 200
    assert r.json()["verification_enabled"] is False

    # presets should 404 for missing preset
    r = client.post("/presets/run", json={"preset_id": "missing"})
    assert r.status_code == 404


def test_agent_execute_allowlist_rejects_unknown_agent(monkeypatch):
    monkeypatch.setenv("TOROIDBOT_ADMIN_KEY", "test-admin-key")
    r = client.post(
        "/agents/not-an-agent/execute",
        headers={"X-API-Key": "test-admin-key"},
        json={"user_prompt": "test"},
    )
    assert r.status_code == 404
