from pathlib import Path
import sqlite3

import typer

from huntbot2.apply.linkedin_adapter import analyze_linkedin_page
from huntbot2.apply.queue_processor import fetch_pending, process_item, queue_snapshot_counts
from huntbot2.apply.runner import run_apply_demo
from huntbot2.automation.pipeline import run_pipeline_now
from huntbot2.automation.preflight import run_preflight
from huntbot2.automation.scheduler import build_automation_plan, build_proactive_checkin_plan
from huntbot2.config.loader import load_config
from huntbot2.db.schema import init_db
from huntbot2.discovery.linkedin import discover_from_search_html
from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.store import upsert_jobs
from huntbot2.scoring.engine import score_job
from huntbot2.scoring.export import export_shortlist_markdown
from huntbot2.tailoring.cover import generate_cover_note
from huntbot2.tailoring.provenance import build_provenance
from huntbot2.tailoring.resume import generate_resume_variant
from huntbot2.tailoring.store import save_artifact

app = typer.Typer(help="HuntBot 2.0 CLI")


@app.command()
def init(project_dir: str = "."):
    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    init_db(db_path)
    typer.echo(f"Initialized DB at {db_path}")


@app.command()
def doctor(project_dir: str = "."):
    report = run_preflight(project_dir)
    for check in report["checks"]:
        level = "WARN" if check.get("optional") else "PASS" if check["ok"] else "FAIL"
        if check.get("optional") and check["ok"]:
            level = "PASS"
        typer.echo(f"{level} {check['name']}: {check['detail']}")

    if report["optional_warnings"]:
        typer.echo("Optional warnings detected; core pipeline can still run with degraded behavior.")

    if not report["ok"]:
        raise typer.Exit(code=1)


@app.command("discover-demo")
def discover_demo(project_dir: str = "."):
    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    fixture_path = root / "fixtures" / "sample_linkedin_search.html"

    init_db(db_path)
    html = fixture_path.read_text()
    jobs = discover_from_search_html(html=html, source_url="https://www.linkedin.com/jobs/search")
    counts = upsert_jobs(db_path=db_path, jobs=jobs)
    typer.echo(
        f"Discovered {len(jobs)} jobs | inserted={counts['inserted']} skipped={counts['skipped']} invalid={counts['invalid']}"
    )


@app.command("shortlist")
def shortlist(project_dir: str = "."):
    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    config = load_config(root / "config" / "profile.yml")

    conn = sqlite3.connect(db_path)
    rows: list[dict] = []
    try:
        for r in conn.execute(
            "SELECT source, title, company, location, url, posted_at, raw_snapshot FROM jobs"
        ).fetchall():
            job = JobRecord(
                source=r[0], title=r[1], company=r[2] or "", location=r[3] or "", url=r[4] or "", posted_at=r[5], raw_snapshot=r[6]
            )
            scored = score_job(job, config)
            if scored["reject_reason"] is None:
                rows.append(
                    {
                        "score": scored["score"],
                        "title": job.title,
                        "company": job.company,
                        "location": job.location,
                        "url": job.url,
                        "reasons": scored["reasons"],
                    }
                )
    finally:
        conn.close()

    rows.sort(key=lambda x: x["score"], reverse=True)
    out = export_shortlist_markdown(project_dir=root, rows=rows)
    typer.echo(f"Exported shortlist: {out}")


@app.command("tailor-demo")
def tailor_demo(project_dir: str = "."):
    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    init_db(db_path)

    config = load_config(root / "config" / "profile.yml")

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, title, company, location, url, source FROM jobs ORDER BY id ASC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise typer.BadParameter("No jobs found. Run discover-demo first.")

    job = {
        "id": row[0],
        "title": row[1] or "",
        "company": row[2] or "",
        "location": row[3] or "",
        "url": row[4] or "",
        "source": row[5] or "",
    }

    master_resume = "# Resume\n\n## Experience\n- Built reliable automation systems."
    resume_variant = generate_resume_variant(master_resume, job, style="concise")
    cover_note = generate_cover_note(job, config.profile.roles)

    resume_meta = {
        "artifact": "resume_variant",
        "job_id": job["id"],
        "provenance": build_provenance("tailor.resume", "v1", {"job": job, "style": "concise"}),
    }
    cover_meta = {
        "artifact": "cover_note",
        "job_id": job["id"],
        "provenance": build_provenance("tailor.cover", "v1", {"job": job, "roles": config.profile.roles}),
    }

    resume_path = save_artifact(root, run_id=None, kind="resume", content=resume_variant, extension="md", meta=resume_meta)
    cover_path = save_artifact(root, run_id=None, kind="cover", content=cover_note, extension="md", meta=cover_meta)

    typer.echo(f"Saved resume: {resume_path}")
    typer.echo(f"Saved cover: {cover_path}")


@app.command("apply-demo")
def apply_demo(
    project_dir: str = ".",
    mode: str = "headed",
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    submit_policy: str = "never_submit",
    submit_token: str | None = None,
    use_browser_probe: bool = typer.Option(False, "--browser-probe/--no-browser-probe"),
):
    result = run_apply_demo(
        project_dir=project_dir,
        mode=mode,
        dry_run=dry_run,
        submit_policy=submit_policy,
        submit_token=submit_token,
        use_browser_probe=use_browser_probe,
    )
    typer.echo(
        f"Apply demo status={result.status} mode={result.mode} dry_run={str(result.dry_run).lower()} "
        f"stopped_before_submit={str(result.stopped_before_submit).lower()}"
    )
    typer.echo(f"Gate decision: {result.gate_decision} policy={result.submit_policy}")
    if result.failure_code:
        typer.echo(f"Failure code: {result.failure_code}")
    if use_browser_probe:
        typer.echo(
            f"Browser probe: status={result.probe_status} adapter_available={str(bool(result.probe_adapter_available)).lower()}"
        )
        if result.probe_screenshot_path:
            typer.echo(f"Browser probe screenshot: {result.probe_screenshot_path}")
    if result.decision_summary:
        typer.echo(f"Decision: {result.decision_summary}")
    if result.queue_name and result.queue_code:
        typer.echo(f"Queue: {result.queue_name} ({result.queue_code})")
    if result.notes:
        typer.echo(f"Notes: {result.notes}")
    typer.echo("Evidence:")
    for path in result.evidence_paths:
        typer.echo(f"- {path}")


@app.command("queue-list")
def queue_list(project_dir: str = ".", status: str = "pending", limit: int = 20):
    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, run_id, queue_name, queue_code, next_action, status, attempts, updated_at
            FROM queue_items
            WHERE status = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    finally:
        conn.close()

    typer.echo(f"Queue items status={status} count={len(rows)}")
    for row in rows:
        typer.echo(
            f"- id={row[0]} run_id={row[1]} queue={row[2]} ({row[3]}) next_action={row[4]} "
            f"status={row[5]} attempts={row[6]} updated_at={row[7]}"
        )


@app.command("queue-run")
def queue_run(project_dir: str = ".", limit: int = 10):
    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    init_db(db_path)

    pending = fetch_pending(db_path=db_path, limit=limit)
    if not pending:
        typer.echo("No pending queue items.")
        snapshot = queue_snapshot_counts(db_path)
        typer.echo(f"Snapshot: {snapshot}")
        return

    for item in pending:
        result = process_item(db_path=db_path, item_id=int(item["id"]), policy="safe")
        typer.echo(
            f"Processed id={result['item_id']} status={result['status']} attempts={result['attempts']} note={result['note']}"
        )

    snapshot = queue_snapshot_counts(db_path)
    typer.echo(
        "Summary: "
        f"pending={snapshot['pending']} processing={snapshot['processing']} blocked={snapshot['blocked']} "
        f"resolved={snapshot['resolved']} failed={snapshot['failed']}"
    )


@app.command("automation-plan")
def automation_plan(project_dir: str = "."):
    Path(project_dir).resolve()  # keep command shape consistent with other CLI commands
    plan = build_automation_plan()
    checkin = build_proactive_checkin_plan()
    typer.echo("Automation plan (UTC):")
    for entry in plan:
        typer.echo(f"- {entry.time_utc} :: {' -> '.join(entry.stages)}")
    typer.echo("Proactive check-in (UTC):")
    typer.echo(f"- {checkin.time_utc} :: {checkin.prompt}")


@app.command("pipeline-now")
def pipeline_now(
    project_dir: str = ".",
    mode: str = "headed",
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    max_retries: int = 1,
    submit_policy: str = "never_submit",
    submit_token: str | None = None,
):
    result = run_pipeline_now(
        project_dir=project_dir,
        mode=mode,
        dry_run=dry_run,
        max_retries=max_retries,
        submit_policy=submit_policy,
        submit_token=submit_token,
    )

    typer.echo(
        f"Pipeline status={result['status']} mode={result['mode']} dry_run={str(result['dry_run']).lower()} "
        f"max_retries={result['max_retries']}"
    )
    for stage in result["stages"]:
        typer.echo(
            f"- {stage['stage']}: {stage['status']} duration_ms={stage['duration_ms']} retries_used={stage['retries_used']}"
        )
    if result.get("apply_gate_decision"):
        typer.echo(
            f"Apply gate decision={result['apply_gate_decision']} policy={result.get('submit_policy')}"
        )
    if result.get("apply_queue_name") and result.get("apply_queue_code"):
        typer.echo(f"Apply queue={result['apply_queue_name']} ({result['apply_queue_code']})")
    typer.echo(f"Digest: {result['digest_path']}")
    typer.echo(f"Observability: {result['observability_path']}")


@app.command("latest-digest")
def latest_digest(project_dir: str = "."):
    root = Path(project_dir).resolve()
    artifacts_dir = root / "artifacts"
    digests = sorted(artifacts_dir.glob("pipeline-digest-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not digests:
        raise typer.BadParameter("No pipeline digest files found.")

    latest = digests[0]
    typer.echo(f"Latest digest: {latest}")
    lines = latest.read_text().splitlines()
    for line in lines[:6]:
        typer.echo(line)


@app.command("linkedin-diagnose")
def linkedin_diagnose(
    html_path: str = typer.Option(..., "--html-path"),
    final_url: str = typer.Option("", "--final-url"),
):
    html = Path(html_path).read_text(encoding="utf-8")
    analysis = analyze_linkedin_page(html=html, final_url=final_url)
    typer.echo(f"login_state={analysis['login_state']}")
    typer.echo(f"apply_path={analysis['apply_path']}")
    typer.echo("signals:")
    for signal in analysis["signals"]:
        typer.echo(f"- {signal}")


if __name__ == "__main__":
    app()
