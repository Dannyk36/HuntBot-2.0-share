from pathlib import Path
import sqlite3
from datetime import datetime, UTC

from huntbot2.discovery.dedup import dedup_key_for_job
from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.normalize import (
    normalize_company,
    normalize_location,
    normalize_title,
    normalize_url,
)


def upsert_jobs(db_path: str | Path, jobs: list[JobRecord]) -> dict[str, int]:
    conn = sqlite3.connect(Path(db_path))
    inserted = 0
    skipped = 0
    invalid = 0
    try:
        for job in jobs:
            normalized = JobRecord(
                source=job.source,
                title=normalize_title(job.title),
                company=normalize_company(job.company),
                location=normalize_location(job.location),
                url=normalize_url(job.url),
                posted_at=job.posted_at,
                raw_snapshot=job.raw_snapshot,
            )
            if not normalized.title or not normalized.url:
                invalid += 1
                continue

            dedup_key = dedup_key_for_job(normalized)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    source, title, company, location, url, posted_at, dedup_key, raw_snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.source,
                    normalized.title,
                    normalized.company,
                    normalized.location,
                    normalized.url,
                    normalized.posted_at,
                    dedup_key,
                    normalized.raw_snapshot,
                    datetime.now(UTC).isoformat(),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        conn.commit()
    finally:
        conn.close()
    return {"inserted": inserted, "skipped": skipped, "invalid": invalid}
