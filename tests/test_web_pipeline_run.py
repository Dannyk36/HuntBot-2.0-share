# tests/test_web_pipeline_run.py
import pytest
import os
from pathlib import Path
from fastapi.testclient import TestClient
import huntbot2.config.env_store as env_store
from huntbot2.db.schema import init_db


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTBOT2_DB_PATH", str(tmp_path / "data" / "huntbot2.db"))
    monkeypatch.setenv("HUNTBOT2_DATA_ROOT", str(tmp_path))
    _original_env_path = env_store._ENV_PATH
    env_store._ENV_PATH = tmp_path / ".env"
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    init_db(tmp_path / "data" / "huntbot2.db")
    (tmp_path / "fixtures").mkdir(exist_ok=True)
    (tmp_path / "fixtures" / "sample_linkedin_search.html").write_text("<html></html>")
    yield
    from huntbot2.web.app import _pipeline_runs
    _pipeline_runs.clear()
    env_store._ENV_PATH = _original_env_path


def test_pipeline_run_returns_run_id():
    from huntbot2.web.app import app
    client = TestClient(app)
    r = client.post("/api/pipeline/run", json={"stage": "discover"})
    assert r.status_code == 200
    d = r.json()
    assert "run_id" in d
    assert d["status"] == "started"


def test_pipeline_run_invalid_stage():
    from huntbot2.web.app import app
    client = TestClient(app)
    r = client.post("/api/pipeline/run", json={"stage": "invalid"})
    assert r.status_code == 422


def test_pipeline_stream_unknown_run_id():
    from huntbot2.web.app import app
    client = TestClient(app)
    with client.stream("GET", "/api/pipeline/run/nonexistent/stream") as resp:
        resp.read()
        text = resp.text
    assert "run not found" in text


def test_add_url_invalid_url():
    from huntbot2.web.app import app
    client = TestClient(app)
    r = client.post("/api/jobs/add-url", json={"url": "not-a-url"})
    assert r.status_code == 200
    assert "error" in r.json()


def test_add_url_playwright_unavailable(monkeypatch):
    import huntbot2.web.app as web_app
    monkeypatch.setattr(web_app, "_add_url_via_playwright", lambda url: None)
    from huntbot2.web.app import app
    client = TestClient(app)
    r = client.post("/api/jobs/add-url", json={"url": "https://linkedin.com/jobs/view/123"})
    assert r.status_code == 200
    d = r.json()
    # Playwright unavailable — falls back to URL as title, still inserts
    assert "job_id" in d


def test_add_url_duplicate():
    from huntbot2.web.app import app
    client = TestClient(app)
    url = "https://linkedin.com/jobs/view/999"
    r1 = client.post("/api/jobs/add-url", json={"url": url})
    assert r1.status_code == 200
    job_id = r1.json()["job_id"]
    r2 = client.post("/api/jobs/add-url", json={"url": url})
    assert r2.status_code == 200
    d = r2.json()
    assert d["duplicate"] is True
    assert d["job_id"] == job_id
    assert "location" in d
