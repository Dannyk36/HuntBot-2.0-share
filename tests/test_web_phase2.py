import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from huntbot2.db.schema import init_db


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "data" / "huntbot2.db"
    init_db(p)
    return p


@pytest.fixture
def populated_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (source, title, company, location, url, created_at, score, score_reasons_json, status, sponsorship_confidence) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("linkedin", "Data Analyst", "Acme", "Remote", "https://acme.example", "2026-01-01", 80, '["role keyword match"]', "queued", "high"),
    )
    conn.execute(
        "INSERT INTO jobs (source, title, company, location, url, created_at, score, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("indeed", "Ops Analyst", "Globex", "NYC", "https://globex.example", "2026-01-02", 60, "scored"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(populated_db, monkeypatch):
    import os
    monkeypatch.setenv("HUNTBOT2_DB_PATH", str(populated_db))
    monkeypatch.setenv("HUNTBOT2_COCKPIT_AUTH_BYPASS", "1")
    import importlib
    import huntbot2.web.app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient as TC
    return TC(app_module.app)


def test_funnel_returns_counts(client):
    r = client.get("/api/pipeline/funnel")
    assert r.status_code == 200
    d = r.json()
    assert "discovered" in d
    assert "scored" in d
    assert "queued" in d
    # Cumulative funnel: a job at 'queued' has also passed through 'scored' and
    # 'discovered'. Fixture has 1 scored + 1 queued, so queued=1, scored=2, discovered=2.
    assert d["queued"] == 1
    assert d["scored"] == 2
    assert d["discovered"] == 2


def test_jobs_list_all(client):
    r = client.get("/api/pipeline/jobs")
    assert r.status_code == 200
    d = r.json()
    assert len(d["jobs"]) == 2
    assert d["total"] == 2


def test_jobs_list_filter_status(client):
    r = client.get("/api/pipeline/jobs?status=queued")
    assert r.status_code == 200
    d = r.json()
    assert len(d["jobs"]) == 1
    assert d["jobs"][0]["status"] == "queued"


def test_job_detail_found(client):
    list_r = client.get("/api/pipeline/jobs")
    job_id = list_r.json()["jobs"][0]["job_id"]
    r = client.get(f"/api/pipeline/jobs/{job_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["job_id"] == job_id
    assert "score_reasons" in d
    assert "artifacts" in d


def test_job_detail_not_found(client):
    r = client.get("/api/pipeline/jobs/99999")
    assert r.status_code == 404
