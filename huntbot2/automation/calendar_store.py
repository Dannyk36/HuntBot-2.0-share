"""Persistence for weekly auto-apply calendar entries."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from huntbot2.automation.calendar_schedule import MODES, PLATFORMS, CalendarEntry
from huntbot2.db.schema import init_db


def _row_to_entry(row: sqlite3.Row) -> CalendarEntry:
    return CalendarEntry(
        id=int(row["id"]),
        day_of_week=int(row["day_of_week"]),
        time_hhmm=str(row["time_hhmm"]),
        platform=str(row["platform"]),
        jobs_per_run=int(row["jobs_per_run"]),
        mode=str(row["mode"]),
        enabled=bool(row["enabled"]),
        last_run_at=row["last_run_at"],
    )


def list_entries(db_path: str | Path) -> list[CalendarEntry]:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM auto_apply_entries ORDER BY day_of_week, time_hhmm, platform"
        ).fetchall()
        return [_row_to_entry(r) for r in rows]
    finally:
        conn.close()


def add_entry(
    db_path: str | Path,
    *,
    day_of_week: int,
    time_hhmm: str,
    platform: str,
    jobs_per_run: int = 5,
    mode: str = "supervised",
    enabled: bool = True,
) -> CalendarEntry:
    if not 0 <= day_of_week <= 6:
        raise ValueError("day_of_week must be 0..6 (Monday..Sunday)")
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    if mode not in MODES:
        raise ValueError(f"unsupported mode: {mode}")
    init_db(db_path)
    now = datetime.now(UTC).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO auto_apply_entries "
            "(day_of_week, time_hhmm, platform, jobs_per_run, mode, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (day_of_week, time_hhmm, platform, max(0, int(jobs_per_run)), mode,
             1 if enabled else 0, now, now),
        )
        conn.commit()
        entry_id = int(cur.lastrowid)
    finally:
        conn.close()
    return CalendarEntry(
        id=entry_id, day_of_week=day_of_week, time_hhmm=time_hhmm, platform=platform,
        jobs_per_run=max(0, int(jobs_per_run)), mode=mode, enabled=enabled, last_run_at=None,
    )


def update_entry(db_path: str | Path, entry_id: int, **fields) -> None:
    allowed = {"day_of_week", "time_hhmm", "platform", "jobs_per_run", "mode", "enabled"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "enabled":
            value = 1 if value else 0
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(datetime.now(UTC).isoformat())
    params.append(entry_id)
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"UPDATE auto_apply_entries SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def delete_entry(db_path: str | Path, entry_id: int) -> None:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM auto_apply_entries WHERE id = ?", (entry_id,))
        conn.commit()
    finally:
        conn.close()


def mark_entry_ran(db_path: str | Path, entry_id: int, when: datetime | None = None) -> None:
    init_db(db_path)
    when = when or datetime.now(UTC)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE auto_apply_entries SET last_run_at = ?, updated_at = ? WHERE id = ?",
            (when.isoformat(), datetime.now(UTC).isoformat(), entry_id),
        )
        conn.commit()
    finally:
        conn.close()
