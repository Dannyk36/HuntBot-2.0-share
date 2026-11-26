from pathlib import Path

from typer.testing import CliRunner

from huntbot2.automation.scheduler import build_automation_plan
from huntbot2.cli.main import app


runner = CliRunner()


def test_build_automation_plan_defaults():
    plan = build_automation_plan()
    assert [entry.time_utc for entry in plan] == ["09:00", "13:00", "18:00"]
    assert all(entry.stages == ["discover", "score", "tailor", "apply"] for entry in plan)


def test_automation_plan_cli_output(tmp_path: Path):
    result = runner.invoke(app, ["automation-plan", "--project-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Automation plan (UTC):" in result.stdout
    assert "09:00 :: discover -> score -> tailor -> apply" in result.stdout
    assert "13:00 :: discover -> score -> tailor -> apply" in result.stdout
    assert "18:00 :: discover -> score -> tailor -> apply" in result.stdout
