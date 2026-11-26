from pathlib import Path

from typer.testing import CliRunner

from huntbot2.cli.main import app


runner = CliRunner()


def _seed_project(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fixtures").mkdir(parents=True, exist_ok=True)

    (tmp_path / "config" / "profile.yml").write_text(
        "profile:\n"
        "  roles: [data analyst]\n"
        "  locations: [remote]\n"
        "runtime:\n"
        "  default_mode: headed\n"
        "  max_retries: 1\n"
    )

    repo_root = Path(__file__).resolve().parents[1]
    fixture_src = repo_root / "fixtures" / "sample_linkedin_search.html"
    (tmp_path / "fixtures" / "sample_linkedin_search.html").write_text(fixture_src.read_text())


def test_pipeline_now_cli_and_latest_digest(tmp_path: Path, monkeypatch):
    _seed_project(tmp_path)
    monkeypatch.setenv("HUNTBOT2_USE_FIXTURE_DISCOVERY", "1")

    run_result = runner.invoke(
        app,
        ["pipeline-now", "--project-dir", str(tmp_path), "--dry-run", "--mode", "headed", "--max-retries", "1"],
    )
    assert run_result.exit_code == 0
    assert "Pipeline status=completed" in run_result.stdout
    assert "Apply gate decision=stopped_before_submit policy=never_submit" in run_result.stdout
    assert "Digest:" in run_result.stdout
    assert "Observability:" in run_result.stdout

    latest_result = runner.invoke(app, ["latest-digest", "--project-dir", str(tmp_path)])
    assert latest_result.exit_code == 0
    assert "Latest digest:" in latest_result.stdout
    assert "# HuntBot Pipeline Digest" in latest_result.stdout


def test_pipeline_now_cli_prints_apply_queue_when_present(tmp_path: Path, monkeypatch):
    _seed_project(tmp_path)

    def fake_run_pipeline_now(**kwargs):
        return {
            "run_id": "pipeline-test",
            "status": "completed",
            "mode": "headed",
            "dry_run": True,
            "max_retries": 0,
            "submit_policy": "never_submit",
            "apply_gate_decision": "stopped_before_submit",
            "apply_queue_name": "draft_fill_queue",
            "apply_queue_code": "Q_DRAFT_FILL",
            "stages": [
                {"stage": "discover", "status": "completed", "duration_ms": 1, "retries_used": 0},
                {"stage": "score", "status": "completed", "duration_ms": 1, "retries_used": 0},
                {"stage": "tailor", "status": "completed", "duration_ms": 1, "retries_used": 0},
                {"stage": "apply", "status": "completed", "duration_ms": 1, "retries_used": 0},
            ],
            "digest_path": str(tmp_path / "artifacts" / "pipeline-digest-test.md"),
            "observability_path": str(tmp_path / "artifacts" / "pipeline-observability-test.json"),
        }

    monkeypatch.setattr("huntbot2.cli.main.run_pipeline_now", fake_run_pipeline_now)
    run_result = runner.invoke(app, ["pipeline-now", "--project-dir", str(tmp_path), "--dry-run"])
    assert run_result.exit_code == 0
    assert "Apply queue=draft_fill_queue (Q_DRAFT_FILL)" in run_result.stdout
