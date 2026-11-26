import json
import sqlite3
from pathlib import Path

from huntbot2.db.schema import init_db
from huntbot2.tailoring.store import save_artifact


def test_save_artifact_writes_file_and_records_db(tmp_path: Path):
    db_path = tmp_path / "data" / "huntbot2.db"
    init_db(db_path)

    meta = {"provenance": {"prompt_id": "p1", "template_version": "v1"}}
    out = save_artifact(
        project_dir=tmp_path,
        run_id=7,
        kind="resume",
        content="hello",
        extension="md",
        meta=meta,
    )

    assert out.exists()
    assert out.read_text() == "hello"

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT run_id, kind, path, meta_json FROM artifacts ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == 7
    assert row[1] == "resume"
    assert row[2] == str(out)
    assert json.loads(row[3]) == meta
