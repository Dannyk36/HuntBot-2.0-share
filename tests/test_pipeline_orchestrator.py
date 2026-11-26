import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from huntbot2.apply.runner import ApplyResult
from huntbot2.automation import pipeline
from huntbot2.automation.pipeline import _stage_tailor, run_pipeline_now
from huntbot2.db.schema import init_db


@pytest.fixture(autouse=True)
def _enable_fixture_discovery(monkeypatch):
    monkeypatch.setenv("HUNTBOT2_USE_FIXTURE_DISCOVERY", "1")


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


def test_pipeline_dry_run_success_creates_digest(tmp_path: Path):
    _seed_project(tmp_path)

    result = run_pipeline_now(project_dir=str(tmp_path), mode="headed", dry_run=True, max_retries=1)

    assert result["status"] == "completed"
    assert [s["stage"] for s in result["stages"]] == ["discover", "score", "tailor", "apply"]
    assert all("duration_ms" in s for s in result["stages"])
    assert all("retries_used" in s for s in result["stages"])
    assert all("backoff_schedule_ms" in s for s in result["stages"])

    digest_path = Path(result["digest_path"])
    assert digest_path.exists()
    content = digest_path.read_text()
    assert "# HuntBot Pipeline Digest" in content
    assert "| discover | completed |" in content
    assert "| apply | completed |" in content
    assert "Apply gate decision: stopped_before_submit" in content
    assert "Apply queue: n/a" in content
    assert "## Queue Snapshot" in content
    assert "- pending:" in content


def test_pipeline_non_dry_run_policy_block_reflected_in_digest(tmp_path: Path):
    _seed_project(tmp_path)

    result = run_pipeline_now(project_dir=str(tmp_path), mode="headed", dry_run=False, max_retries=0)
    digest = Path(result["digest_path"]).read_text()

    # policy_blocked is a safety-gate stop, not a real failure — pipeline completes
    assert result["status"] == "completed"
    apply_stage = [s for s in result["stages"] if s["stage"] == "apply"][0]
    assert apply_stage["status"] == "completed"
    # Gate decision is recorded in the result and digest
    assert result["apply_gate_decision"] in ("stopped_before_submit", "policy_blocked")
    assert "policy" in digest.lower()


def test_pipeline_digest_includes_gate_decision_for_allow_submit(tmp_path: Path):
    _seed_project(tmp_path)

    result = run_pipeline_now(
        project_dir=str(tmp_path),
        mode="headed",
        dry_run=False,
        max_retries=0,
        submit_policy="allow_submit_with_token",
        submit_token="I_UNDERSTAND_SUBMIT",
    )
    digest = Path(result["digest_path"]).read_text()

    assert result["status"] == "completed"
    assert result["apply_gate_decision"] == "would_submit"
    assert "Apply gate decision: would_submit" in digest


def test_pipeline_retry_accounting_present(tmp_path: Path, monkeypatch):
    _seed_project(tmp_path)

    original_discover = pipeline.STAGE_RUNNERS["discover"]
    state = {"count": 0}

    def flaky_discover(project_dir: Path, mode: str, dry_run: bool):
        if state["count"] == 0:
            state["count"] += 1
            raise RuntimeError("transient discovery failure")
        return original_discover(project_dir, mode, dry_run)

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "discover", flaky_discover)

    result = run_pipeline_now(project_dir=str(tmp_path), max_retries=1)

    discover = [s for s in result["stages"] if s["stage"] == "discover"][0]
    assert result["status"] == "completed"
    assert discover["status"] == "completed"
    assert discover["retries_used"] == 1
    assert discover["backoff_schedule_ms"] == [250]
    assert discover["next_backoff_ms"] == 250


def test_pipeline_observability_artifact_written(tmp_path: Path):
    _seed_project(tmp_path)

    result = run_pipeline_now(project_dir=str(tmp_path), mode="headed", dry_run=True, max_retries=1)

    observability_path = Path(result["observability_path"])
    assert observability_path.exists()
    assert observability_path.name.startswith("pipeline-observability-")

    payload = json.loads(observability_path.read_text())
    assert "run_id" in payload
    assert "timestamp_utc" in payload
    assert "stage_metrics" in payload
    assert "total_retries" in payload
    assert "failures" in payload
    assert "queue_snapshot" in payload
    assert isinstance(payload["stage_metrics"], list)


def test_pipeline_digest_includes_apply_decision_summary_when_present(tmp_path: Path, monkeypatch):
    _seed_project(tmp_path)

    def fake_run_apply_demo(**kwargs):
        return ApplyResult(
            status="completed",
            mode="headed",
            dry_run=True,
            submit_policy="never_submit",
            gate_decision="stopped_before_submit",
            stopped_before_submit=True,
            failure_code=None,
            evidence_paths=[str(tmp_path / "artifacts" / "apply-step-open-run-0001.txt")],
            notes="ok",
            decision_next_action="proceed_draft_fill",
            decision_summary="decision=proceed_draft_fill (logged_in/easy_apply)",
            queue_name="draft_fill_queue",
            queue_code="Q_DRAFT_FILL",
        )

    monkeypatch.setattr("huntbot2.automation.pipeline.run_apply_demo", fake_run_apply_demo)

    result = run_pipeline_now(project_dir=str(tmp_path), mode="headed", dry_run=True, max_retries=0)
    digest = Path(result["digest_path"]).read_text()
    assert result["apply_queue_name"] == "draft_fill_queue"
    assert result["apply_queue_code"] == "Q_DRAFT_FILL"
    assert "Apply decision: decision=proceed_draft_fill (logged_in/easy_apply)" in digest
    assert "Decision route context: decision=proceed_draft_fill (logged_in/easy_apply)" in digest
    assert "Queue route: draft_fill_queue (Q_DRAFT_FILL)" in digest


# ── Tailor stage must never downgrade applied/queued jobs ──────────────────────

def _seed_tailor_project(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "profile.yml").write_text(
        "profile:\n"
        "  roles: [data analyst]\n"
        "  locations: [remote]\n"
        "runtime:\n"
        "  default_mode: headed\n"
        "  max_retries: 1\n"
    )
    db_path = tmp_path / "data" / "huntbot2.db"
    init_db(db_path)
    return db_path


def _insert_job(db_path: Path, *, job_id: int, status: str, score: float, url: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO jobs (id, source, title, company, location, url, dedup_key, "
            "created_at, score, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, "test", f"Job {job_id}", "Acme", "Remote", url, f"dedup-{job_id}",
             datetime.now(UTC).isoformat(), score, status),
        )
        conn.commit()
    finally:
        conn.close()


def _status_of(db_path: Path, job_id: int) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    finally:
        conn.close()


def test_tailor_fallback_skips_applied_jobs(tmp_path: Path, monkeypatch):
    """When no 'scored' jobs remain, the re-tailor fallback must select only
    scored/tailored jobs — never applied/queued ones."""
    db_path = _seed_tailor_project(tmp_path)
    # No 'scored' jobs left → forces the fallback re-tailor query.
    _insert_job(db_path, job_id=1, status="applied", score=100, url="https://x/applied")
    _insert_job(db_path, job_id=2, status="tailored", score=95, url="https://x/tailored")

    tailored_ids: list[int] = []

    def spy_tailor_one_job(job, project_dir, db, config, master_resume, llm_client=None):
        tailored_ids.append(job["id"])
        return {"resume_path": "r.pdf", "cover_path": "c.pdf"}

    monkeypatch.setattr(pipeline, "_tailor_one_job", spy_tailor_one_job)

    _stage_tailor(tmp_path, "headless", False)

    assert 1 not in tailored_ids, "applied job must not be re-tailored"
    assert tailored_ids == [2]
    assert _status_of(db_path, 1) == "applied"


def test_tailor_does_not_downgrade_status(tmp_path: Path, monkeypatch):
    """With only applied/queued jobs present, tailor finds nothing to do and
    must raise rather than reverting their status."""
    db_path = _seed_tailor_project(tmp_path)
    _insert_job(db_path, job_id=1, status="applied", score=100, url="https://x/applied")
    _insert_job(db_path, job_id=2, status="queued", score=90, url="https://x/queued")

    def fail_if_called(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("_tailor_one_job should not run for applied/queued jobs")

    monkeypatch.setattr(pipeline, "_tailor_one_job", fail_if_called)

    with pytest.raises(RuntimeError, match="No scored jobs"):
        _stage_tailor(tmp_path, "headless", False)

    assert _status_of(db_path, 1) == "applied"
    assert _status_of(db_path, 2) == "queued"


def test_tailor_one_job_update_guard_preserves_applied(tmp_path: Path, monkeypatch):
    """Defense in depth: even if _tailor_one_job runs on an applied job, its
    status UPDATE must not downgrade it to 'tailored'."""
    from huntbot2.config.loader import load_config

    db_path = _seed_tailor_project(tmp_path)
    _insert_job(db_path, job_id=1, status="applied", score=100, url="")

    # Stub out rendering + artifact registration so the test stays offline and
    # exercises only the status-update guard.
    monkeypatch.setattr(pipeline, "register_artifact", lambda *a, **k: None)
    monkeypatch.setattr("huntbot2.tailoring.pdf.render_resume_to_pdf", lambda *a, **k: None)
    monkeypatch.setattr("huntbot2.tailoring.pdf.render_cover_to_pdf", lambda *a, **k: None)

    config = load_config(tmp_path / "config" / "profile.yml")
    job = {"id": 1, "title": "Job 1", "company": "Acme", "location": "Remote",
           "url": "", "source": "test", "score_keywords": []}

    pipeline._tailor_one_job(job, tmp_path, db_path, config, "# Resume", llm_client=None)

    assert _status_of(db_path, 1) == "applied"
