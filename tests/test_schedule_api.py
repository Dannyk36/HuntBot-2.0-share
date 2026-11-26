from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import huntbot2.config.env_store as env_store
import huntbot2.web.app as web_app
from huntbot2.db.schema import init_db

client = TestClient(web_app.app)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTBOT2_DB_PATH", str(tmp_path / "data" / "huntbot2.db"))
    monkeypatch.setenv("HUNTBOT2_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HUNTBOT2_DISABLE_SCHEDULER", "1")
    env_store._ENV_PATH = tmp_path / ".env"
    init_db(tmp_path / "data" / "huntbot2.db")
    with web_app._scheduler_lock:
        web_app._scheduler_state["last_run_at"] = None
        web_app._scheduler_state["last_result"] = None
    yield


def _insert_tailored(db, n):
    import sqlite3
    conn = sqlite3.connect(db)
    for i in range(n):
        conn.execute(
            "INSERT INTO jobs (source, title, company, location, url, created_at, dedup_key, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("test", f"Job {i}", "Acme", "Remote", f"https://x/{i}",
             datetime.now(UTC).isoformat(), f"k{i}", "tailored"),
        )
    conn.commit()
    conn.close()


def test_schedule_defaults():
    d = client.get("/api/schedule").json()
    assert d["enabled"] is False
    assert d["jobs_per_run"] == 5
    assert d["interval_minutes"] == 180


def test_schedule_update_persists():
    r = client.post("/api/schedule", json={"enabled": True, "jobs_per_run": 3, "interval_minutes": 120})
    assert r.status_code == 200
    d = client.get("/api/schedule").json()
    assert d["enabled"] is True
    assert d["jobs_per_run"] == 3
    assert d["interval_minutes"] == 120


def test_schedule_update_validates_window_format():
    r = client.post("/api/schedule", json={"window_start": "8am"})
    assert r.status_code == 422


def test_schedule_status_reports_capacity(tmp_path):
    _insert_tailored(web_app._db_path(), 4)
    s = client.get("/api/schedule/status").json()
    assert s["available_tailored"] == 4
    assert s["applied_today"] == 0
    assert "next_run_at" in s


def test_decide_and_run_skips_when_disabled():
    result = web_app._scheduler_decide_and_run(now=datetime(2026, 6, 11, 12, tzinfo=UTC))
    assert result == {"ran": False, "reason": "not_due"}


def test_decide_and_run_triggers_when_due(monkeypatch):
    _insert_tailored(web_app._db_path(), 10)
    env_store.set("HUNTBOT2_SCHEDULE_ENABLED", "true")
    env_store.set("HUNTBOT2_SCHEDULE_JOBS_PER_RUN", "3")
    env_store.set("HUNTBOT2_SCHEDULE_WINDOW_START", "00:00")
    env_store.set("HUNTBOT2_SCHEDULE_WINDOW_END", "23:59")

    started = {}

    def fake_thread_target(run_id, run, stage, project_dir, mode="auto"):
        started["run_id"] = run_id
        started["stage"] = stage

    monkeypatch.setattr(web_app, "_run_pipeline_thread", fake_thread_target)

    result = web_app._scheduler_decide_and_run(now=datetime(2026, 6, 11, 12, tzinfo=UTC))
    assert result["ran"] is True
    assert result["applied_planned"] == 3  # jobs_per_run, under cap and availability


def test_run_now_forces_a_cycle_ignoring_window(monkeypatch):
    _insert_tailored(web_app._db_path(), 10)
    # Disabled + outside any sensible window, but force should still run.
    env_store.set("HUNTBOT2_SCHEDULE_ENABLED", "false")
    env_store.set("HUNTBOT2_SCHEDULE_JOBS_PER_RUN", "2")
    monkeypatch.setattr(web_app, "_run_pipeline_thread", lambda *a, **k: None)

    r = client.post("/api/schedule/run-now").json()
    assert r["ran"] is True
    assert r["applied_planned"] == 2


def test_decide_and_run_nothing_to_apply(monkeypatch):
    env_store.set("HUNTBOT2_SCHEDULE_ENABLED", "true")
    env_store.set("HUNTBOT2_SCHEDULE_WINDOW_START", "00:00")
    env_store.set("HUNTBOT2_SCHEDULE_WINDOW_END", "23:59")
    # No tailored jobs available → plan is 0.
    result = web_app._scheduler_decide_and_run(now=datetime(2026, 6, 11, 12, tzinfo=UTC))
    assert result["ran"] is True
    assert result["applied_planned"] == 0
    assert result["reason"] == "nothing_to_apply"
