import time
import json

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
