from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from huntbot2.apply.queue_processor import queue_snapshot_counts
from huntbot2.apply.runner import run_apply_demo
from huntbot2.automation.digest import write_pipeline_digest
from huntbot2.automation.observability import write_pipeline_observability
from huntbot2.automation.preflight import run_preflight
from huntbot2.config.loader import load_config
from huntbot2.db.schema import init_db
from huntbot2.discovery.linkedin import discover_from_search_html
from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.store import upsert_jobs
from huntbot2.scoring.engine import score_job
from huntbot2.scoring.export import export_shortlist_markdown
from huntbot2.scoring.store import persist_score
from huntbot2.tailoring.cover import generate_cover_note
from huntbot2.tailoring.provenance import build_provenance
from huntbot2.tailoring.resume import generate_resume_variant
from huntbot2.tailoring.store import register_artifact


def _stage_discover(project_dir: Path, mode: str, dry_run: bool) -> dict:
    import json as _json
    import os
    db_path = project_dir / "data" / "huntbot2.db"
    init_db(db_path)

    jobs = []
    live = os.getenv("HUNTBOT2_LIVE_DISCOVER", "false").lower() == "true"
    if live:
        config = load_config(project_dir / "config" / "profile.yml")
        roles = getattr(getattr(config, "profile", None), "roles", [])
        locations = getattr(getattr(config, "profile", None), "locations", [])
        portals_env = os.getenv("HUNTBOT2_PORTALS", "")
        if portals_env:
            try:
                portals = _json.loads(portals_env)
            except Exception:
                portals = ["linkedin"]
        else:
            portals = getattr(config, "portals", []) or ["linkedin"]

        extra_urls: list[str] = []
        raw_urls = os.getenv("HUNTBOT2_DISCOVERY_URLS", "")
        if raw_urls:
            try:
                extra_urls = _json.loads(raw_urls)
            except Exception:
                pass

        display = os.getenv("HUNTBOT2_XVFB_DISPLAY", ":99")
        from huntbot2.discovery.live import _display_ready
        headed = _display_ready(display)  # always headed when display available
        use_llm_discover = os.getenv("HUNTBOT2_LLM_ENABLED", "false").lower() == "true"

        if use_llm_discover:
            from huntbot2.discovery.agent import discover_with_agent
            from huntbot2.llm.client import client_for_discovery
            llm = client_for_discovery()
            if llm:
                jobs = discover_with_agent(
                    roles=roles,
                    locations=locations,
                    llm_client=llm,
                    extra_urls=extra_urls,
                    platforms=portals,
                    headed=headed,
                    display=display,
                    max_jobs=int(os.getenv("HUNTBOT2_DISCOVER_MAX_JOBS", "40")),
                    max_urls=int(os.getenv("HUNTBOT2_DISCOVER_MAX_URLS", "4")),
                    max_actions=int(os.getenv("HUNTBOT2_DISCOVER_MAX_ACTIONS", "15")),
                )
            else:
                use_llm_discover = False

        if not use_llm_discover:
            from huntbot2.discovery.live import discover_live
            jobs = discover_live(
                roles=roles,
                locations=locations,
                extra_urls=extra_urls,
                platforms=portals,
                headed=headed,
                display=display,
                slow_mo=600 if headed else 0,
            )

    # Only fall back to the bundled fixture HTML when explicitly opted in
    # (tests, demos). Silently seeding fake jobs into the user's DB was
    # confusing apply runs by pointing the browser at fake URLs.
    used_fixture = False
    if not jobs and os.getenv("HUNTBOT2_USE_FIXTURE_DISCOVERY") == "1":
        fixture_path = project_dir / "fixtures" / "sample_linkedin_search.html"
        if fixture_path.exists():
            html = fixture_path.read_text()
            jobs = discover_from_search_html(html=html, source_url="https://www.linkedin.com/jobs/search")
            used_fixture = True

    counts = upsert_jobs(db_path=db_path, jobs=jobs)
    if used_fixture:
        source = "fixture"
    elif live and jobs:
        source = "live"
    else:
        source = "none"
    return {"jobs_discovered": len(jobs), "source": source, **counts}


def _stage_score(project_dir: Path, mode: str, dry_run: bool, llm_client=None) -> dict:
    db_path = project_dir / "data" / "huntbot2.db"
    config = load_config(project_dir / "config" / "profile.yml")

    if llm_client is not None:
        from huntbot2.scoring.engine import score_job_llm as _score_fn
    else:
        _score_fn = None

    conn = sqlite3.connect(db_path)
    rows: list[dict] = []
    already_scored = 0
    try:
        already_scored = conn.execute("SELECT COUNT(*) FROM jobs WHERE score IS NOT NULL").fetchone()[0]
        for r in conn.execute(
            "SELECT id, source, title, company, location, url, posted_at, raw_snapshot FROM jobs WHERE score IS NULL"
        ).fetchall():
            job = JobRecord(
                source=r[1],
                title=r[2],
                company=r[3] or "",
                location=r[4] or "",
                url=r[5] or "",
                posted_at=r[6],
                raw_snapshot=r[7],
            )
            if llm_client is not None and _score_fn is not None:
                scored = _score_fn(job, config, llm_client)
            else:
                scored = score_job(job, config)
            persist_score(db_path=db_path, job_id=r[0], score_result=scored)
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
    out = export_shortlist_markdown(project_dir=project_dir, rows=rows)
    return {"shortlist_count": len(rows), "already_scored": already_scored, "shortlist_path": str(out)}


def _fetch_and_store_description(job: dict, db_path: Path) -> str:
    """Return job description from DB; if missing, fetch from URL and store it."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT description FROM jobs WHERE id=?", [job["id"]]).fetchone()
        stored = (row[0] or "").strip() if row else ""
    finally:
        conn.close()

    if stored:
        return stored

    url = job.get("url", "")
    if not url:
        return ""

    from huntbot2.discovery.job_detail import fetch_job_description
    description = fetch_job_description(url) or ""

    if description:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE jobs SET description=? WHERE id=?", [description, job["id"]])
            conn.commit()
        finally:
            conn.close()

    return description


def _validate_job_url(url: str, stored_title: str, job_id: int, db_path: Path) -> tuple[str, str]:
    """Return ("ok", "") or ("stale_url", reason). Marks job in DB on stale."""
    if not url or not url.startswith(("http://", "https://")):
        return "ok", ""

    is_linkedin = "linkedin.com" in url.lower()

    # Non-LinkedIn job boards (Indeed, Glassdoor, etc.) are accessed via a
    # logged-in browser during apply — httpx fetches fail bot-detection and
    # linkedin_adapter signals are irrelevant.  Skip network validation for them.
    if not is_linkedin:
        return "ok", ""

    from huntbot2.apply.linkedin_adapter import detect_apply_path

    try:
        import httpx
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = httpx.get(url, headers=headers, timeout=12.0, follow_redirects=True)
        if not resp.is_success:
            return "stale_url", f"http_{resp.status_code}"
        html = resp.text
        final_url = str(resp.url)
    except Exception as exc:
        return "stale_url", f"fetch_error:{type(exc).__name__}"

    if detect_apply_path(html, final_url) == "unavailable":
        return "stale_url", "page_unavailable"

    # Loose title-mismatch check via JSON-LD (works on public LinkedIn pages)
    if stored_title and is_linkedin:
        import json as _json
        import re as _re
        for m in _re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, _re.DOTALL | _re.IGNORECASE,
        ):
            try:
                data = _json.loads(m.group(1))
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("@type") == "JobPosting":
                        page_title = (entry.get("title") or "").lower().strip()
                        our_title = stored_title.lower().strip()
                        if page_title and our_title and page_title not in our_title and our_title not in page_title:
                            return "stale_url", f"title_mismatch:{page_title!r}"
            except Exception:
                continue

    return "ok", ""


def _mark_stale(db_path: Path, job_id: int, reason: str) -> None:
    from datetime import UTC, datetime as _dt
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status='stale_url', updated_at=? WHERE id=?",
            (_dt.now(UTC).isoformat(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _tailor_one_job(
    job: dict,
    project_dir: Path,
    db_path: Path,
    config,
    master_resume: str,
    llm_client=None,
) -> dict:
    """Tailor resume + cover for one job, register artifacts, mark job tailored."""
    from datetime import UTC, datetime as _dt

    # Enrich job dict with full description + score keywords before LLM calls
    job["description"] = _fetch_and_store_description(job, db_path)

    resume_meta = {
        "artifact": "resume_variant",
        "job_id": job["id"],
        "provenance": build_provenance("tailor.resume", "v1", {"job": job}),
    }
    cover_meta = {
        "artifact": "cover_note",
        "job_id": job["id"],
        "provenance": build_provenance("tailor.cover", "v1", {"job": job}),
    }

    ts = _dt.now(UTC).strftime("%Y%m%d%H%M%S")
    artifacts_dir = project_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    resume_path = artifacts_dir / f"resume-{ts}.pdf"
    cover_path = artifacts_dir / f"cover-{ts}.pdf"

    cover_template = project_dir / "config" / "cover_letter_template.docx"
    resume_template = project_dir / "config" / "resume_template.docx"

    if cover_template.exists() and resume_template.exists():
        from huntbot2.tailoring.docx_renderer import fill_cover_to_pdf, tailor_resume_to_pdf
        tailor_resume_to_pdf(job, resume_template, resume_path, llm_client=llm_client, master_resume_md=master_resume)
        fill_cover_to_pdf(job, cover_template, cover_path, llm_client=llm_client, project_dir=project_dir)
    else:
        from huntbot2.tailoring.pdf import render_cover_to_pdf, render_resume_to_pdf
        if llm_client is not None:
            from huntbot2.tailoring.resume import generate_resume_variant_llm
            from huntbot2.tailoring.cover import generate_cover_note_llm
            resume_variant = generate_resume_variant_llm(master_resume, job, llm_client)
            cover_note = generate_cover_note_llm(job, config.profile.roles, llm_client, project_dir=project_dir)
        else:
            resume_variant = generate_resume_variant(master_resume, job, style="concise")
            cover_note = generate_cover_note(job, config.profile.roles)
        render_resume_to_pdf(resume_variant, resume_path)
        render_cover_to_pdf(cover_note, cover_path)

    register_artifact(db_path, resume_path, run_id=None, kind="resume", meta=resume_meta)
    register_artifact(db_path, cover_path, run_id=None, kind="cover", meta=cover_meta)

    conn = sqlite3.connect(db_path)
    try:
        # Never downgrade a job that has already progressed past tailoring.
        # Re-tailoring an applied/queued job would otherwise revert its status
        # and make it eligible for re-application.
        conn.execute(
            "UPDATE jobs SET status='tailored', updated_at=? "
            "WHERE id=? AND status NOT IN ('applied', 'queued')",
            (_dt.now(UTC).isoformat(), job["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    return {"resume_path": str(resume_path), "cover_path": str(cover_path)}


def _stage_tailor(project_dir: Path, mode: str, dry_run: bool, llm_client=None) -> dict:
    import json as _json
    import os as _os
    db_path = project_dir / "data" / "huntbot2.db"
    config = load_config(project_dir / "config" / "profile.yml")
    batch_size = int(_os.getenv("HUNTBOT2_TAILOR_BATCH_SIZE", "5"))
    try:
        min_score = float(_os.getenv("HUNTBOT2_MIN_SCORE", "0") or "0")
    except (ValueError, TypeError):
        min_score = 0.0

    conn = sqlite3.connect(db_path)
    try:
        # Pick highest-scored jobs that haven't been tailored yet, gated by min_score
        rows_db = conn.execute(
            """SELECT id, title, company, location, url, source, score_reasons_json
               FROM jobs
               WHERE status = 'scored' AND COALESCE(score, 0) >= ?
               ORDER BY score DESC NULLS LAST LIMIT ?""",
            [min_score, batch_size],
        ).fetchall()
        if not rows_db:
            # All scored jobs above threshold already tailored — re-tailor the
            # top jobs that still meet the threshold. Restrict to scored/tailored
            # only: never pull in applied/queued jobs (re-tailoring would revert
            # their status) or rejected 'discovered' jobs.
            rows_db = conn.execute(
                """SELECT id, title, company, location, url, source, score_reasons_json
                   FROM jobs
                   WHERE status IN ('scored', 'tailored') AND COALESCE(score, 0) >= ?
                   ORDER BY score DESC NULLS LAST LIMIT ?""",
                [min_score, batch_size],
            ).fetchall()
    finally:
        conn.close()

    if not rows_db:
        msg = f"No scored jobs at or above min_score={min_score:.0f}."
        if min_score > 0:
            msg += " Lower HUNTBOT2_MIN_SCORE in Settings or run score stage on more jobs."
        else:
            msg += " Run score stage first."
        raise RuntimeError(msg)

    master_resume_path = project_dir / "config" / "master_resume.md"
    master_resume = (
        master_resume_path.read_text()
        if master_resume_path.exists()
        else "# Resume\n\n## Experience\n- Built reliable automation systems."
    )

    tailored = 0
    last_resume = ""
    last_cover = ""
    for row in rows_db:
        score_keywords: list[str] = []
        try:
            score_keywords = _json.loads(row[6] or "[]")
        except Exception:
            pass
        job = {
            "id": row[0], "title": row[1] or "", "company": row[2] or "",
            "location": row[3] or "", "url": row[4] or "", "source": row[5] or "",
            "score_keywords": score_keywords,
        }
        verdict, reason = _validate_job_url(job["url"], job["title"], job["id"], db_path)
        if verdict == "stale_url":
            _mark_stale(db_path, job["id"], reason)
            continue
        result = _tailor_one_job(job, project_dir, db_path, config, master_resume, llm_client=llm_client)
        last_resume = result["resume_path"]
        last_cover = result["cover_path"]
        tailored += 1

    return {
        "tailored_count": tailored,
        "resume_path": last_resume,
        "cover_path": last_cover,
        "job_title": rows_db[-1][1] or "" if rows_db else "",
        "job_company": rows_db[-1][2] or "" if rows_db else "",
    }


def _stage_apply(
    project_dir: Path,
    mode: str,
    dry_run: bool,
    submit_policy: str = "never_submit",
    submit_token: str | None = None,
    llm_client=None,
) -> dict:
    result = run_apply_demo(
        project_dir=project_dir,
        mode=mode,
        dry_run=dry_run,
        submit_policy=submit_policy,
        submit_token=submit_token,
        llm_client=llm_client,
    )
    # policy_blocked is expected safety-gate behavior, not a real failure
    if result.status != "completed" and result.failure_code != "policy_blocked":
        raise RuntimeError(result.failure_code or result.notes or "apply stage failed")

    # Per-job status='applied' is set inside the browser applier when a submit
    # actually succeeded — don't bulk-update here.
    if result.gate_decision in ("go", "submitted", "would_submit"):
        import os as _os
        db_path = project_dir / "data" / "huntbot2.db"
        from huntbot2.tailoring.store import prune_artifacts
        keep = int(_os.getenv("HUNTBOT2_ARTIFACT_KEEP", "50"))
        prune_artifacts(db_path, keep=keep)

    return {
        "apply_status": result.status,
        "evidence_paths": result.evidence_paths,
        "gate_decision": result.gate_decision,
        "submit_policy": result.submit_policy,
        "decision_summary": result.decision_summary,
        "queue_name": result.queue_name,
        "queue_code": result.queue_code,
    }


def _stage_session(
    project_dir: Path,
    mode: str,
    dry_run: bool,
    llm_client=None,
    llm_tailor_client=None,
) -> dict:
    """Run the full pipeline in a single VNC browser session."""
    from huntbot2.session.orchestrator import run_vnc_session
    return run_vnc_session(
        project_dir,
        mode=mode,
        submit=not dry_run,
        llm_client=llm_client,
        llm_tailor_client=llm_tailor_client,
    )


STAGE_ORDER = ["discover", "score", "tailor", "apply"]
STAGE_RUNNERS = {
    "discover": _stage_discover,
    "score": _stage_score,
    "tailor": _stage_tailor,
    "apply": _stage_apply,
    "session": _stage_session,
}


def run_pipeline_now(
    project_dir: str,
    mode: str = "headed",
    dry_run: bool = True,
    max_retries: int = 1,
    apply_backoff_sleep: bool = False,
    submit_policy: str = "never_submit",
    submit_token: str | None = None,
    use_llm: bool = False,
) -> dict:
    root = Path(project_dir).resolve()
    init_db(root / "data" / "huntbot2.db")
    preflight = run_preflight(root)
    run_id = f"pipeline-{uuid4().hex[:12]}"

    score_client = None
    tailor_client = None
    if use_llm:
        from huntbot2.llm.client import client_from_env, client_for_tailoring
        score_client = client_from_env()
        tailor_client = client_for_tailoring()
    llm_client = score_client  # kept for backwards-compat with existing callers

    stage_results: list[dict] = []
    pipeline_status = "completed"
    apply_gate_decision: str | None = None
    apply_decision_summary: str | None = None
    apply_queue_name: str | None = None
    apply_queue_code: str | None = None
    queue_snapshot: dict[str, int] | None = None

    for stage_name in STAGE_ORDER:
        retries_used = 0
        start_ns = time.perf_counter_ns()
        error: str | None = None
        status = "completed"
        backoff_schedule_ms: list[int] = []
        next_backoff_ms: int | None = None

        for attempt in range(max_retries + 1):
            try:
                if stage_name == "apply":
                    apply_result = _stage_apply(
                        root,
                        mode,
                        dry_run,
                        submit_policy=submit_policy,
                        submit_token=submit_token,
                    )
                    apply_gate_decision = apply_result["gate_decision"]
                    apply_decision_summary = apply_result.get("decision_summary")
                    apply_queue_name = apply_result.get("queue_name")
                    apply_queue_code = apply_result.get("queue_code")
                elif stage_name == "score" and score_client is not None:
                    _stage_score(root, mode, dry_run, llm_client=score_client)
                elif stage_name == "tailor" and tailor_client is not None:
                    _stage_tailor(root, mode, dry_run, llm_client=tailor_client)
                else:
                    STAGE_RUNNERS[stage_name](root, mode, dry_run)
                retries_used = attempt
                break
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                retries_used = attempt
                if attempt >= max_retries:
                    next_backoff_ms = None
                    status = "failed"
                    break
                next_backoff_ms = 250 * (2 ** attempt)
                backoff_schedule_ms.append(next_backoff_ms)
                if apply_backoff_sleep:
                    time.sleep(next_backoff_ms / 1000)

        duration_ms = int((time.perf_counter_ns() - start_ns) / 1_000_000)
        stage_results.append(
            {
                "stage": stage_name,
                "status": status,
                "duration_ms": duration_ms,
                "error": error,
                "retries_used": retries_used,
                "backoff_schedule_ms": backoff_schedule_ms,
                "next_backoff_ms": next_backoff_ms,
                "queue_name": apply_queue_name if stage_name == "apply" else None,
                "queue_code": apply_queue_code if stage_name == "apply" else None,
            }
        )

        if status == "failed":
            pipeline_status = "failed"
            break

    completed_stages = {s["stage"] for s in stage_results}
    for stage_name in STAGE_ORDER:
        if stage_name not in completed_stages:
            stage_results.append(
                {
                    "stage": stage_name,
                    "status": "skipped",
                    "duration_ms": 0,
                    "error": "skipped due to earlier stage failure",
                    "retries_used": 0,
                    "backoff_schedule_ms": [],
                    "next_backoff_ms": None,
                }
            )

    queue_snapshot = queue_snapshot_counts(root / "data" / "huntbot2.db")

    improvement_hooks: list[str] = []
    for stage in stage_results:
        if stage.get("status") == "failed":
            improvement_hooks.append(
                f"Patch or refine workflow for stage `{stage['stage']}` to prevent: {stage.get('error') or 'unknown error'}"
            )
    for warning in preflight.get("optional_warnings", []):
        improvement_hooks.append(
            f"Optional dependency gap `{warning['name']}`: {warning['detail']}"
        )
    if apply_decision_summary:
        improvement_hooks.append("Review apply decision route and convert repeated decisions into reusable skill guidance.")

    digest_path = write_pipeline_digest(
        project_dir=root,
        mode=mode,
        dry_run=dry_run,
        stage_results=stage_results,
        apply_gate_decision=apply_gate_decision,
        submit_policy=submit_policy,
        apply_decision_summary=apply_decision_summary,
        apply_queue_name=apply_queue_name,
        apply_queue_code=apply_queue_code,
        queue_snapshot=queue_snapshot,
        improvement_hooks=improvement_hooks,
    )

    observability_path = write_pipeline_observability(
        project_dir=root,
        run_summary={
            "run_id": run_id,
            "status": pipeline_status,
            "mode": mode,
            "dry_run": dry_run,
            "max_retries": max_retries,
            "submit_policy": submit_policy,
            "apply_gate_decision": apply_gate_decision,
            "apply_queue_name": apply_queue_name,
            "apply_queue_code": apply_queue_code,
            "queue_snapshot": queue_snapshot,
            "stages": stage_results,
        },
    )

    llm_call_log_path: str | None = None
    if llm_client is not None and llm_client.calls:
        llm_log = root / "artifacts" / f"llm-calls-{run_id}.jsonl"
        llm_client.write_call_log(llm_log)
        llm_call_log_path = str(llm_log)

    return {
        "run_id": run_id,
        "status": pipeline_status,
        "mode": mode,
        "dry_run": dry_run,
        "max_retries": max_retries,
        "submit_policy": submit_policy,
        "apply_gate_decision": apply_gate_decision,
        "apply_queue_name": apply_queue_name,
        "apply_queue_code": apply_queue_code,
        "stages": stage_results,
        "digest_path": str(digest_path),
        "observability_path": str(observability_path),
        "llm_call_log_path": llm_call_log_path,
        "llm_call_count": len(llm_client.calls) if llm_client else 0,
        "preflight": preflight,
    }
