from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUSES = {RUN_STATUS_RUNNING, RUN_STATUS_COMPLETED, RUN_STATUS_FAILED}

FAILURE_CODES = {
    "auth_expired",
    "captcha_blocked",
    "selector_changed",
    "required_field_unknown",
    "file_upload_failed",
    "duplicate_application",
    "policy_blocked",
}


@dataclass
class RunRecord:
    id: int
    run_type: str
    status: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize_notes(notes: str | dict | list | None) -> str | None:
    if notes is None:
        return None
    if isinstance(notes, str):
        return notes
    return json.dumps(notes, separators=(",", ":"))


def start_run(db_path: str | Path, run_type: str) -> RunRecord:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO runs (run_type, status, started_at) VALUES (?, ?, ?)",
            (run_type, RUN_STATUS_RUNNING, _now_iso()),
        )
        conn.commit()
        return RunRecord(id=cur.lastrowid, run_type=run_type, status=RUN_STATUS_RUNNING)
    finally:
        conn.close()


def finish_run(
    db_path: str | Path,
    run_id: int,
    status: str = RUN_STATUS_COMPLETED,
    failure_code: str | None = None,
    notes: str | dict | list | None = None,
) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(f"Invalid run status: {status}")
    if failure_code is not None and failure_code not in FAILURE_CODES:
        raise ValueError(f"Invalid failure code: {failure_code}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ?, failure_code = ?, notes = ? WHERE id = ?",
            (status, _now_iso(), failure_code, _serialize_notes(notes), run_id),
        )
        conn.commit()
    finally:
        conn.close()
