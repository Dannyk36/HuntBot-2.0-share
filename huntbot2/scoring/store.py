from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def persist_score(
    *,
    db_path: str | Path,
    job_id: int,
    score_result: dict,
) -> None:
    """Write scoring engine output back to the jobs row."""
    score = score_result.get("score", 0)
    reasons = score_result.get("reasons", [])
    reject_reason = score_result.get("reject_reason")
    sponsorship = score_result.get("sponsorship_confidence", "unknown")
    status = "discovered" if reject_reason else "scored"
    now = datetime.now(UTC).isoformat()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET score=?, score_reasons_json=?, status=?, "
            "sponsorship_confidence=?, updated_at=? WHERE id=?",
            (score, json.dumps(reasons), status, sponsorship, now, job_id),
        )
        conn.commit()
    finally:
        conn.close()
