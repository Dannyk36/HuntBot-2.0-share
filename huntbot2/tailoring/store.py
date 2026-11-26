from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from huntbot2.db.schema import init_db

_DEFAULT_KEEP = 50


def save_artifact(
    project_dir: str | Path,
    run_id: int | None,
    kind: str,
    content: str,
    extension: str,
    meta: dict,
) -> Path:
    root = Path(project_dir).resolve()
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    clean_ext = extension.lstrip(".")
    run_tag = str(run_id) if run_id is not None else "none"
    filename = f"{kind}-{run_tag}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.{clean_ext}"
    out_path = artifacts_dir / filename
    out_path.write_text(content)

    db_path = root / "data" / "huntbot2.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO artifacts (run_id, kind, path, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                kind,
                str(out_path),
                json.dumps(meta, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return out_path


def register_artifact(
    db_path: str | Path,
    path: Path,
    *,
    run_id: int | None,
    kind: str,
    meta: dict,
) -> None:
    """Register a pre-written artifact file in the DB without writing any content."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO artifacts (run_id, kind, path, meta_json, created_at) VALUES (?,?,?,?,?)",
            (run_id, kind, str(path), json.dumps(meta, sort_keys=True), datetime.now(UTC).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def prune_artifacts(
    db_path: str | Path,
    *,
    keep: int = _DEFAULT_KEEP,
) -> dict:
    """Delete artifact files and DB rows beyond the most recent `keep` entries.

    Returns counts of what was removed so callers can log the result.
    """
    conn = sqlite3.connect(db_path)
    deleted_files = 0
    deleted_rows = 0
    try:
        to_delete = conn.execute(
            "SELECT id, path FROM artifacts WHERE id NOT IN "
            "(SELECT id FROM artifacts ORDER BY id DESC LIMIT ?)",
            (keep,),
        ).fetchall()

        for row_id, path_str in to_delete:
            if path_str:
                p = Path(path_str)
                if p.exists():
                    try:
                        p.unlink()
                        deleted_files += 1
                    except OSError:
                        pass
            conn.execute("DELETE FROM artifacts WHERE id = ?", (row_id,))
            deleted_rows += 1

        conn.commit()
    finally:
        conn.close()

    return {"deleted_files": deleted_files, "deleted_rows": deleted_rows}
