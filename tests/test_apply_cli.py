import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from huntbot2.cli.main import app
from huntbot2.apply.runner import ApplyResult


runner = CliRunner()


def test_apply_demo_cli_dry_run(tmp_path: Path):
    result = runner.invoke(app, ["apply-demo", "--project-dir", str(tmp_path), "--dry-run", "--mode", "headed"])

    assert result.exit_code == 0
    assert "status=completed" in result.stdout
    assert "dry_run=true" in result.stdout
    assert "Gate decision: stopped_before_submit policy=never_submit" in result.stdout
    assert "Evidence:" in result.stdout

    conn = sqlite3.connect(tmp_path / "data" / "huntbot2.db")
    try:
        run_row = conn.execute("SELECT status, failure_code FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    assert run_row == ("completed", None)
    assert len(list((tmp_path / "artifacts").glob("apply-step-*-run-*.txt"))) == 1
    assert len(list((tmp_path / "artifacts").glob("apply-step-*-run-*.md"))) == 1


def test_apply_demo_cli_no_dry_run_policy_blocked(tmp_path: Path):
    result = runner.invoke(app, ["apply-demo", "--project-dir", str(tmp_path), "--no-dry-run"])

    assert result.exit_code == 0
    assert "status=failed" in result.stdout
    assert "Gate decision: stopped_before_submit policy=never_submit" in result.stdout
    assert "Failure code: policy_blocked" in result.stdout

    conn = sqlite3.connect(tmp_path / "data" / "huntbot2.db")
    try:
        run_row = conn.execute("SELECT status, failure_code, notes FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    assert run_row[0] == "failed"
    assert run_row[1] == "policy_blocked"
    assert "policy gate" in run_row[2]


def test_apply_demo_cli_allow_submit_with_valid_token(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "apply-demo",
            "--project-dir",
            str(tmp_path),
            "--no-dry-run",
            "--submit-policy",
            "allow_submit_with_token",
            "--submit-token",
            "I_UNDERSTAND_SUBMIT",
        ],
    )

    assert result.exit_code == 0
    assert "status=completed" in result.stdout
    assert "Gate decision: would_submit policy=allow_submit_with_token" in result.stdout


def test_apply_demo_cli_browser_probe_dry_run_completes(tmp_path: Path):
    init_result = runner.invoke(app, ["apply-demo", "--project-dir", str(tmp_path), "--dry-run"])
    assert init_result.exit_code == 0

    conn = sqlite3.connect(tmp_path / "data" / "huntbot2.db")
    try:
        conn.execute(
            """
            INSERT INTO jobs (source, title, company, location, url, posted_at, dedup_key, raw_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "test",
                "CLI Probe Job",
                "ACME",
                "Remote",
                "https://example.invalid/job/cli",
                None,
                "probe-cli-1",
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(
        app,
        ["apply-demo", "--project-dir", str(tmp_path), "--dry-run", "--browser-probe", "--mode", "headless"],
    )

    assert result.exit_code == 0
    assert "status=completed" in result.stdout
    assert "Browser probe: status=" in result.stdout
    assert "adapter_available=" in result.stdout

    decision_artifacts = list((tmp_path / "artifacts").glob("apply-decision-run-*.json"))
    assert len(decision_artifacts) == 1
    assert '"next_action"' in decision_artifacts[0].read_text()


def test_apply_demo_cli_prints_browser_probe_screenshot_when_available(tmp_path: Path, monkeypatch):
    screenshot_path = str(tmp_path / "artifacts" / "apply-probe-run-0001.png")

    def fake_run_apply_demo(**kwargs):
        return ApplyResult(
            status="completed",
            mode="headless",
            dry_run=True,
            submit_policy="never_submit",
            gate_decision="stopped_before_submit",
            stopped_before_submit=True,
            failure_code=None,
            evidence_paths=[str(tmp_path / "artifacts" / "apply-step-open-run-0001.txt")],
            notes="ok",
            probe_status="completed",
            probe_adapter_available=True,
            probe_screenshot_path=screenshot_path,
            decision_next_action="proceed_draft_fill",
            decision_summary="decision=proceed_draft_fill (logged_in/easy_apply)",
            queue_name="draft_fill_queue",
            queue_code="Q_DRAFT_FILL",
        )

    monkeypatch.setattr("huntbot2.cli.main.run_apply_demo", fake_run_apply_demo)

    result = runner.invoke(app, ["apply-demo", "--project-dir", str(tmp_path), "--dry-run", "--browser-probe"])

    assert result.exit_code == 0
    assert "Browser probe screenshot:" in result.stdout
    assert screenshot_path in result.stdout
    assert "Decision: decision=proceed_draft_fill (logged_in/easy_apply)" in result.stdout
    assert "Queue: draft_fill_queue (Q_DRAFT_FILL)" in result.stdout
