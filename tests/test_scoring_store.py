import json
import sqlite3
import tempfile
from pathlib import Path
from huntbot2.db.schema import init_db
from huntbot2.scoring.store import persist_score


def test_jobs_has_pipeline_columns():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "data" / "huntbot2.db"
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        conn.close()
        assert "score" in cols
        assert "score_reasons_json" in cols
        assert "status" in cols
        assert "sponsorship_confidence" in cols
        assert "updated_at" in cols


def test_artifacts_has_job_id_column():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "data" / "huntbot2.db"
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()}
        conn.close()
        assert "job_id" in cols


def test_persist_score_writes_to_db():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "data" / "huntbot2.db"
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (source, title, company, location, url, created_at) VALUES (?,?,?,?,?,?)",
            ("linkedin", "Data Analyst", "Acme", "Remote", "https://acme.example/job", "2026-01-01")
        )
        conn.commit()
        job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
        conn.close()

        persist_score(db_path=db_path, job_id=job_id, score_result={"score": 80, "reasons": ["role keyword match"]})

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT score, score_reasons_json, status FROM jobs WHERE id=?", [job_id]).fetchone()
        conn.close()
        assert row[0] == 80
        assert json.loads(row[1]) == ["role keyword match"]
        assert row[2] == "scored"


def test_persist_score_rejected_job_sets_zero():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "data" / "huntbot2.db"
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (source, title, company, location, url, created_at) VALUES (?,?,?,?,?,?)",
            ("linkedin", "Software Engineer", "Corp", "NYC", "https://corp.example/job", "2026-01-01")
        )
        conn.commit()
        job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
        conn.close()

        persist_score(db_path=db_path, job_id=job_id, score_result={"score": 0, "reasons": [], "reject_reason": "excluded keyword"})

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT score, status FROM jobs WHERE id=?", [job_id]).fetchone()
        conn.close()
        assert row[0] == 0
        assert row[1] == "discovered"  # rejected stays at discovered
