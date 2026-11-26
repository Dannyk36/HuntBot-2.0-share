import sqlite3
from pathlib import Path

from huntbot2.db.schema import init_db
from huntbot2.discovery.dedup import dedup_key_for_job
from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.store import upsert_jobs


def test_dedup_and_store(tmp_path: Path):
    db_path = tmp_path / "data" / "huntbot2.db"
    init_db(db_path)

    job = JobRecord(
        source="linkedin",
        title="Data Analyst",
        company="Acme Corp",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/123?trk=x",
    )
    job_dup = JobRecord(
        source="linkedin",
        title="data analyst",
        company="  acme corp ",
        location="remote",
        url="https://www.linkedin.com/jobs/view/123?trk=y",
    )

    assert dedup_key_for_job(job) == dedup_key_for_job(job_dup)

    c1 = upsert_jobs(db_path, [job])
    c2 = upsert_jobs(db_path, [job_dup])

    assert c1 == {"inserted": 1, "skipped": 0, "invalid": 0}
    assert c2 == {"inserted": 0, "skipped": 1, "invalid": 0}

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_store_normalizes_and_skips_invalid(tmp_path: Path):
    db_path = tmp_path / "data" / "huntbot2.db"
    init_db(db_path)

    good = JobRecord(
        source="linkedin",
        title="  Data   Analyst  ",
        company="  Acme   Corp ",
        location=" Remote  ",
        url="https://www.linkedin.com/jobs/view/999?trk=x",
    )
    invalid = JobRecord(
        source="linkedin",
        title="   ",
        company="Bad",
        location="Remote",
        url="not-a-url",
    )

    counts = upsert_jobs(db_path, [good, invalid])
    assert counts == {"inserted": 1, "skipped": 0, "invalid": 1}

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT title, company, location, url FROM jobs").fetchone()
    finally:
        conn.close()

    assert row == (
        "Data Analyst",
        "Acme Corp",
        "Remote",
        "https://www.linkedin.com/jobs/view/999/",
    )
