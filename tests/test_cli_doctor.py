from pathlib import Path

from typer.testing import CliRunner

from huntbot2.cli.main import app
from huntbot2.db.schema import init_db


runner = CliRunner()


def test_doctor_pass(tmp_path: Path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "profile.yml").write_text(
        "profile:\n  roles: [analyst]\n  locations: [remote]\nruntime:\n  default_mode: headed\n  max_retries: 1\n"
    )
    init_db(tmp_path / "data" / "huntbot2.db")

    result = runner.invoke(app, ["doctor", "--project-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "PASS config" in result.stdout
    assert "PASS db" in result.stdout


def test_doctor_fail(tmp_path: Path):
    result = runner.invoke(app, ["doctor", "--project-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "FAIL config" in result.stdout
    assert "FAIL db" in result.stdout
