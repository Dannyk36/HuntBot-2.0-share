import sqlite3
from pathlib import Path

from huntbot2.apply.runner import run_apply_demo


def test_run_apply_demo_dry_run_completes_and_writes_evidence(tmp_path: Path):
    result = run_apply_demo(project_dir=tmp_path, mode="headed", dry_run=True)

    assert result.status == "completed"
    assert result.dry_run is True
    assert result.stopped_before_submit is True
    assert result.failure_code is None
    assert result.gate_decision == "stopped_before_submit"
    assert len(result.evidence_paths) == 2

    for p in result.evidence_paths:
        path = Path(p)
        assert path.exists()
        assert path.parent == tmp_path / "artifacts"
        text = path.read_text()
        assert "submit_policy: never_submit" in text
        assert "token_present: false" in text


def test_run_apply_demo_allow_submit_bad_token_policy_blocked(tmp_path: Path):
    result = run_apply_demo(
        project_dir=tmp_path,
        mode="headed",
        dry_run=False,
        submit_policy="allow_submit_with_token",
        submit_token="BAD_TOKEN",
    )

    assert result.status == "failed"
    assert result.dry_run is False
    assert result.stopped_before_submit is True
    assert result.failure_code == "policy_blocked"
    assert result.gate_decision == "policy_blocked"


def test_run_apply_demo_allow_submit_valid_token_would_submit(tmp_path: Path):
    result = run_apply_demo(
        project_dir=tmp_path,
        mode="headed",
        dry_run=False,
        submit_policy="allow_submit_with_token",
        submit_token="I_UNDERSTAND_SUBMIT",
    )

    assert result.status == "completed"
    assert result.dry_run is False
    assert result.stopped_before_submit is False
    assert result.failure_code is None
    assert result.gate_decision == "would_submit"

    conn = sqlite3.connect(tmp_path / "data" / "huntbot2.db")
    try:
        row = conn.execute(
            "SELECT run_type, status, failure_code, notes FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == "apply"
    assert row[1] == "completed"
    assert row[2] is None
    assert "browser apply executed" in row[3]


def test_run_apply_demo_browser_probe_adapter_unavailable_is_nonfatal(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "data" / "huntbot2.db"
    result_init = run_apply_demo(project_dir=tmp_path, mode="headed", dry_run=True)
    assert result_init.status == "completed"

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO jobs (source, title, company, location, url, posted_at, dedup_key, raw_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "test",
                "Test Job",
                "ACME",
                "Remote",
                "https://example.invalid/job/1",
                None,
                "probe-test-1",
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        "huntbot2.apply.runner.probe_job_page",
        lambda url, mode="headed", timeout_ms=10000, screenshot_path=None: {
            "ok": False,
            "adapter_available": False,
            "reason": "playwright_unavailable",
            "requested_url": url,
            "mode": mode,
        },
    )

    result = run_apply_demo(project_dir=tmp_path, mode="headed", dry_run=True, use_browser_probe=True)

    assert result.status == "completed"
    assert result.probe_status == "unavailable"
    assert result.probe_adapter_available is False
    assert result.probe_screenshot_path is None
    assert result.queue_name is not None
    assert result.queue_code is not None
    assert any("apply-probe-run-" in p and p.endswith(".json") for p in result.evidence_paths)
    decision_path = [Path(p) for p in result.evidence_paths if "apply-decision-run-" in p and p.endswith(".json")][0]
    payload = decision_path.read_text()
    assert '"next_action"' in payload
    queue_path = [Path(p) for p in result.evidence_paths if "apply-queue-run-" in p and p.endswith(".json")][0]
    queue_payload = queue_path.read_text()
    assert '"queue_name"' in queue_payload
    assert '"queue_code"' in queue_payload
    assert '"run_id"' in queue_payload

    conn = sqlite3.connect(db_path)
    try:
        queue_row = conn.execute(
            "SELECT queue_name, queue_code, next_action, status, attempts, payload_json FROM queue_items ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert queue_row is not None
    assert queue_row[0] == result.queue_name
    assert queue_row[1] == result.queue_code
    assert queue_row[3] == "pending"
    assert queue_row[4] == 0
    assert '"decision_context"' in queue_row[5]


def test_run_apply_demo_browser_probe_captured_screenshot_is_added_to_evidence(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "data" / "huntbot2.db"
    result_init = run_apply_demo(project_dir=tmp_path, mode="headed", dry_run=True)
    assert result_init.status == "completed"

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO jobs (source, title, company, location, url, posted_at, dedup_key, raw_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "test",
                "Screenshot Job",
                "ACME",
                "Remote",
                "https://example.invalid/job/2",
                None,
                "probe-test-2",
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    def fake_probe(url, mode="headed", timeout_ms=10000, screenshot_path=None):
        assert screenshot_path is not None
        screenshot_file = Path(screenshot_path)
        screenshot_file.parent.mkdir(parents=True, exist_ok=True)
        screenshot_file.write_bytes(b"fakepng")
        return {
            "ok": True,
            "adapter_available": True,
            "requested_url": url,
            "final_url": url,
            "mode": mode,
            "screenshot_path": str(screenshot_file),
        }

    monkeypatch.setattr("huntbot2.apply.runner.probe_job_page", fake_probe)

    result = run_apply_demo(project_dir=tmp_path, mode="headless", dry_run=True, use_browser_probe=True)

    assert result.status == "completed"
    assert result.probe_status == "completed"
    assert result.probe_adapter_available is True
    assert result.probe_screenshot_path is not None
    assert result.queue_name is not None
    assert result.queue_code is not None
    screenshot = Path(result.probe_screenshot_path)
    assert screenshot.exists()
    assert str(screenshot) in result.evidence_paths

    probe_json = [Path(p) for p in result.evidence_paths if p.endswith(".json") and "apply-probe-run-" in p][0]
    payload = probe_json.read_text()
    assert '"screenshot_path"' in payload
    assert str(screenshot) in payload

    decision_json = [Path(p) for p in result.evidence_paths if p.endswith(".json") and "apply-decision-run-" in p][0]
    decision_payload = decision_json.read_text()
    assert '"next_action"' in decision_payload
    queue_json = [Path(p) for p in result.evidence_paths if p.endswith(".json") and "apply-queue-run-" in p][0]
    queue_payload = queue_json.read_text()
    assert f'"queue_name": "{result.queue_name}"' in queue_payload
    assert f'"queue_code": "{result.queue_code}"' in queue_payload
