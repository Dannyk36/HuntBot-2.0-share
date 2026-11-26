from pathlib import Path
import sqlite3

from huntbot2.db.schema import init_db
from huntbot2.logging.runs import (
    FAILURE_CODES,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    finish_run,
    start_run,
)


def test_run_lifecycle(tmp_path: Path):
    db = tmp_path / "huntbot2.db"
    init_db(db)

    run = start_run(db, "discover")
    assert run.status == "running"

    finish_run(db, run.id, status=RUN_STATUS_COMPLETED, notes={"result": "ok"})

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT status, notes, finished_at, failure_code FROM runs WHERE id = ?", (run.id,)
    ).fetchone()
    conn.close()

    assert row[0] == RUN_STATUS_COMPLETED
    assert row[1] == '{"result":"ok"}'
    assert row[2] is not None
    assert row[3] is None


def test_run_failure_code_and_notes(tmp_path: Path):
    db = tmp_path / "huntbot2.db"
    init_db(db)

    run = start_run(db, "apply")
    finish_run(
        db,
        run.id,
        status=RUN_STATUS_FAILED,
        failure_code="captcha_blocked",
        notes={"portal": "linkedin"},
    )

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, failure_code, notes FROM runs WHERE id = ?", (run.id,)).fetchone()
    conn.close()

    assert row[0] == RUN_STATUS_FAILED
    assert row[1] == "captcha_blocked"
    assert row[2] == '{"portal":"linkedin"}'
    assert "captcha_blocked" in FAILURE_CODES
