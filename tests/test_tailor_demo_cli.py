import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from huntbot2.cli.main import app
from huntbot2.db.schema import init_db


runner = CliRunner()


def test_tailor_demo_cli_creates_artifacts_and_rows(tmp_path: Path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "profile.yml").write_text(
        "profile:\n  roles: [data analyst]\n  locations: [remote]\nruntime:\n  default_mode: headed\n  max_retries: 1\n"
    )

    db_path = tmp_path / "data" / "huntbot2.db"
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO jobs (source, title, company, location, url, posted_at, dedup_key, raw_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "linkedin",
                "Data Analyst",
                "Acme",
                "Remote",
                "https://example.com/job/1",
                None,
                "demo-key",
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(app, ["tailor-demo", "--project-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Saved resume:" in result.stdout
    assert "Saved cover:" in result.stdout

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT kind, path, meta_json FROM artifacts ORDER BY id ASC").fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    kinds = {r[0] for r in rows}
    assert kinds == {"resume", "cover"}
    for _, path, _ in rows:
        assert Path(path).exists()
