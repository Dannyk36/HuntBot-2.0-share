"""Lightweight structured log store — errors, warnings, pipeline events.

Keeps at most MAX_ENTRIES rows. Older entries are pruned on each write so the
table never bloats. Use clear_logs() to wipe everything manually.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MAX_ENTRIES = 500


def write_log(
    db_path: str | Path,
    *,
    level: str,
    source: str,
    message: str,
    details: dict | None = None,
) -> None:
    """Write one log entry and prune to MAX_ENTRIES."""
    now = datetime.now(UTC).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO app_logs (level, source, message, details, created_at) VALUES (?,?,?,?,?)",
            (level, source, message, json.dumps(details) if details else None, now),
        )
        conn.execute(
            "DELETE FROM app_logs WHERE id NOT IN "
            "(SELECT id FROM app_logs ORDER BY id DESC LIMIT ?)",
            (MAX_ENTRIES,),
        )
        conn.commit()
    except Exception:
        pass  # never crash the caller over logging
    finally:
        conn.close()


def list_logs(
    db_path: str | Path,
    *,
    level: str | None = None,
    source: str | None = None,
    limit: int = 200,
) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        clauses, params = [], []
        if level:
            clauses.append("level = ?")
            params.append(level)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"SELECT id, level, source, message, details, created_at FROM app_logs "
            f"{where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "level": r[1],
            "source": r[2],
            "message": r[3],
            "details": json.loads(r[4]) if r[4] else None,
            "created_at": r[5],
        }
        for r in rows
    ]


def clear_logs(db_path: str | Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("DELETE FROM app_logs")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
