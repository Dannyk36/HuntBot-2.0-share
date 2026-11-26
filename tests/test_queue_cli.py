from pathlib import Path

from typer.testing import CliRunner

from huntbot2.apply.queue_processor import enqueue_item
from huntbot2.cli.main import app


runner = CliRunner()


def test_queue_list_and_run(tmp_path: Path):
    db_path = tmp_path / "data" / "huntbot2.db"
    enqueue_item(
        db_path=db_path,
        run_id="run-0100",
        queue_name="draft_fill_queue",
        queue_code="Q_DRAFT_FILL",
        next_action="proceed_draft_fill",
        payload={"decision_context": {}, "evidence_paths": []},
    )
    enqueue_item(
        db_path=db_path,
        run_id="run-0101",
        queue_name="login_pause_queue",
        queue_code="Q_LOGIN_PAUSE",
        next_action="pause_login",
        payload={"decision_context": {}, "evidence_paths": []},
    )

    listed = runner.invoke(app, ["queue-list", "--project-dir", str(tmp_path), "--status", "pending", "--limit", "20"])
    assert listed.exit_code == 0
    assert "Queue items status=pending count=2" in listed.stdout

    ran = runner.invoke(app, ["queue-run", "--project-dir", str(tmp_path), "--limit", "10"])
    assert ran.exit_code == 0
    assert "Processed id=" in ran.stdout
    assert "Summary: pending=0" in ran.stdout
    assert "blocked=1" in ran.stdout
    assert "resolved=1" in ran.stdout

    listed_after = runner.invoke(app, ["queue-list", "--project-dir", str(tmp_path), "--status", "pending"])
    assert listed_after.exit_code == 0
    assert "count=0" in listed_after.stdout
