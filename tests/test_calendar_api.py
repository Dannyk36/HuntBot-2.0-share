import pytest
from fastapi.testclient import TestClient

import huntbot2.config.env_store as env_store
import huntbot2.web.app as web_app
from huntbot2.automation import calendar_store
from huntbot2.db.schema import init_db

client = TestClient(web_app.app)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTBOT2_DB_PATH", str(tmp_path / "data" / "huntbot2.db"))
    monkeypatch.setenv("HUNTBOT2_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HUNTBOT2_DISABLE_SCHEDULER", "1")
    env_store._ENV_PATH = tmp_path / ".env"
    init_db(tmp_path / "data" / "huntbot2.db")
    # Never launch a real pipeline thread or browser in these tests.
    monkeypatch.setattr(web_app, "_run_pipeline_thread", lambda *a, **k: None)
    monkeypatch.setattr(web_app, "_add_url_via_playwright", lambda url: None)
    yield


# ── store ─────────────────────────────────────────────────────────────────────

def test_store_add_and_list(tmp_path):
    db = web_app._db_path()
    calendar_store.add_entry(db, day_of_week=0, time_hhmm="09:00", platform="linkedin")
    entries = calendar_store.list_entries(db)
    assert len(entries) == 1
    assert entries[0].platform == "linkedin"


def test_store_rejects_bad_inputs(tmp_path):
    db = web_app._db_path()
    with pytest.raises(ValueError):
        calendar_store.add_entry(db, day_of_week=9, time_hhmm="09:00", platform="linkedin")
    with pytest.raises(ValueError):
        calendar_store.add_entry(db, day_of_week=0, time_hhmm="09:00", platform="facebook")


# ── API ───────────────────────────────────────────────────────────────────────

def test_calendar_empty():
    d = client.get("/api/calendar").json()
    assert d["entries"] == []
    assert "linkedin" in d["platforms"]
    assert d["modes"] == ["supervised", "automatic"]
    assert d["day_names"][0] == "Monday"


def test_calendar_add_and_list():
    r = client.post("/api/calendar", json={
        "day_of_week": 0, "time_hhmm": "09:00", "platform": "linkedin",
        "jobs_per_run": 3, "mode": "supervised",
    })
    assert r.status_code == 200
    d = client.get("/api/calendar").json()
    assert len(d["entries"]) == 1
    assert d["entries"][0]["jobs_per_run"] == 3


def test_calendar_rejects_unknown_platform():
    r = client.post("/api/calendar", json={"day_of_week": 0, "time_hhmm": "09:00", "platform": "facebook"})
    assert r.status_code == 422


def test_calendar_rejects_bad_time():
    r = client.post("/api/calendar", json={"day_of_week": 0, "time_hhmm": "9am", "platform": "linkedin"})
    assert r.status_code == 422


def test_calendar_update_and_delete():
    eid = client.post("/api/calendar", json={"day_of_week": 1, "time_hhmm": "10:00", "platform": "indeed"}).json()["id"]
    client.post(f"/api/calendar/{eid}", json={"jobs_per_run": 7, "enabled": False})
    entry = [e for e in client.get("/api/calendar").json()["entries"] if e["id"] == eid][0]
    assert entry["jobs_per_run"] == 7
    assert entry["enabled"] is False
    client.delete(f"/api/calendar/{eid}")
    assert client.get("/api/calendar").json()["entries"] == []


def test_calendar_run_now_supervised():
    eid = client.post("/api/calendar", json={
        "day_of_week": 0, "time_hhmm": "09:00", "platform": "glassdoor", "mode": "supervised",
    }).json()["id"]
    r = client.post(f"/api/calendar/{eid}/run-now")
    assert r.status_code == 200
    body = r.json()
    assert body["ran"] is True
    assert body["platform"] == "glassdoor"
    assert body["mode"] == "supervised"


def test_calendar_run_now_sets_supervised_policy():
    eid = client.post("/api/calendar", json={
        "day_of_week": 0, "time_hhmm": "09:00", "platform": "linkedin", "mode": "supervised",
    }).json()["id"]
    client.post(f"/api/calendar/{eid}/run-now")
    import os
    assert os.environ["HUNTBOT2_SUBMIT_POLICY"] == "stop_before_submit"


def test_apply_url_supervised():
    r = client.post("/api/jobs/apply-url", json={"url": "https://linkedin.com/jobs/view/123", "mode": "supervised"})
    assert r.status_code == 200
    assert "job_id" in r.json()
    assert r.json()["mode"] == "supervised"


def test_apply_url_invalid():
    r = client.post("/api/jobs/apply-url", json={"url": "not-a-url"})
    assert r.json().get("error") == "invalid_url"
