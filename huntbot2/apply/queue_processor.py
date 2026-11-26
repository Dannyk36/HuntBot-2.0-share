from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3
from pathlib import Path

from huntbot2.db.schema import init_db

QUEUE_STATUSES = {"pending", "processing", "blocked", "resolved", "failed"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def enqueue_item(
    db_path: str | Path,
    run_id: str,
    queue_name: str,
    queue_code: str,
    next_action: str,
    payload: dict,
    status: str = "pending",
) -> int:
    if status not in QUEUE_STATUSES:
        raise ValueError(f"Unsupported queue status: {status}")

    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        now = _now_iso()
        cursor = conn.execute(
            """
            INSERT INTO queue_items
            (run_id, queue_name, queue_code, next_action, payload_json, status, attempts, created_at, updated_at, last_error)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)
            """,
            (run_id, queue_name, queue_code, next_action, json.dumps(payload, sort_keys=True), status, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def fetch_pending(db_path: str | Path, limit: int = 10) -> list[dict]:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, run_id, queue_name, queue_code, next_action, payload_json, status, attempts,
                   created_at, updated_at, last_error
            FROM queue_items
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _resolve_queue_result(queue_name: str) -> tuple[str, str]:
    mapping = {
        "draft_fill_queue": ("resolved", "ready_for_draft_fill"),
        "login_pause_queue": ("blocked", "requires_login"),
        "checkpoint_queue": ("blocked", "requires_checkpoint_resolution"),
        "external_handoff_queue": ("resolved", "handoff_packet_created"),
        "manual_review_queue": ("blocked", "requires_manual_review"),
    }
    if queue_name not in mapping:
        raise ValueError(f"Unsupported queue_name: {queue_name}")
    return mapping[queue_name]


def process_item(db_path: str | Path, item_id: int, policy: str = "safe") -> dict:
    if policy != "safe":
        raise ValueError(f"Unsupported policy: {policy}")

    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ValueError(f"Queue item not found: {item_id}")

        now = _now_iso()
        conn.execute(
            "UPDATE queue_items SET status = 'processing', attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (now, item_id),
        )

        updated = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        attempts = int(updated["attempts"])

        try:
            final_status, note = _resolve_queue_result(str(row["queue_name"]))
            conn.execute(
                "UPDATE queue_items SET status = ?, updated_at = ?, last_error = ? WHERE id = ?",
                (final_status, _now_iso(), note, item_id),
            )
        except Exception as exc:  # noqa: BLE001
            conn.execute(
                "UPDATE queue_items SET status = 'failed', updated_at = ?, last_error = ? WHERE id = ?",
                (_now_iso(), str(exc), item_id),
            )
            conn.commit()
            return {"item_id": item_id, "status": "failed", "attempts": attempts, "note": str(exc)}

        conn.commit()
        return {"item_id": item_id, "status": final_status, "attempts": attempts, "note": note}
    finally:
        conn.close()


def queue_snapshot_counts(db_path: str | Path) -> dict[str, int]:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        counts = {status: 0 for status in ["pending", "processing", "blocked", "resolved", "failed"]}
        rows = conn.execute("SELECT status, COUNT(*) FROM queue_items GROUP BY status").fetchall()
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts
    finally:
        conn.close()
