"""Unified VNC session: discover → score → tailor → apply in one browser window.

Opens a single persistent Chromium context (agent-linkedin profile) that stays
visible in VNC from the first job search URL through the last apply submission.
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import time
from pathlib import Path


def run_vnc_session(
    project_dir: str | Path,
    *,
    mode: str = "headed",
    submit: bool = True,
    llm_client=None,
    llm_tailor_client=None,
) -> dict:
    """Run the full pipeline — discover, score, tailor, apply — in one browser.

    Returns a summary dict with counts and paths for each phase.
    """
    from playwright.sync_api import sync_playwright

    from huntbot2.apply.candidate import load_candidate_profile
    from huntbot2.automation.pipeline import _stage_score, _stage_tailor
    from huntbot2.config.loader import load_config
    from huntbot2.db.schema import init_db
    from huntbot2.discovery.live import _display_ready, discover_live
    from huntbot2.discovery.store import upsert_jobs

    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    artifact_dir = root / "artifacts" / "live"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    config = load_config(root / "config" / "profile.yml")
    roles = getattr(getattr(config, "profile", None), "roles", [])
    locations = getattr(getattr(config, "profile", None), "locations", [])
    portals_env = os.getenv("HUNTBOT2_PORTALS", "")
    if portals_env:
        try:
            platforms = _json.loads(portals_env)
        except Exception:
            platforms = ["linkedin"]
    else:
        platforms = getattr(config, "portals", []) or ["linkedin"]

    extra_urls: list[str] = []
    raw_urls = os.getenv("HUNTBOT2_DISCOVERY_URLS", "")
    if raw_urls:
        try:
            extra_urls = _json.loads(raw_urls)
        except Exception:
            pass

    display = os.getenv("HUNTBOT2_XVFB_DISPLAY", ":99")
    use_headed = _display_ready(display)  # always headed when display available

    profile_base = os.environ.get(
        "HUNTBOT2_BROWSER_PROFILE_BASE",
        os.path.expanduser("~/.huntbot2/profiles"),
    )
    profile_dir = os.path.join(profile_base, "agent-linkedin")
    if not os.path.exists(profile_dir):
        profile_dir = os.path.join(profile_base, "linkedin")
    os.makedirs(profile_dir, exist_ok=True)

    candidate_profile = load_candidate_profile(root)
    profile_yaml = ""
    profile_yaml_path = root / "config" / "candidate_profile.yml"
    if profile_yaml_path.exists():
        profile_yaml = profile_yaml_path.read_text()

    master_resume = ""
    resume_path = root / "config" / "master_resume.md"
    if resume_path.exists():
        master_resume = resume_path.read_text()

    from huntbot2.apply.qa_store import load_learned_answers
    try:
        learned_answers = load_learned_answers(root)
    except Exception:
        learned_answers = {}

    summary: dict = {
        "phase_discover": {},
        "phase_score": {},
        "phase_tailor": {},
        "phase_apply": [],
    }

    # ── Phase 1: Discover ────────────────────────────────────────────────────
    # In session mode discovery is intentionally narrow so the full pipeline
    # finishes in a single sitting: LinkedIn only (where Easy Apply lives),
    # first role + first location + remote.  Deeper discovery runs via the
    # regular "discover" stage which is optimised for breadth.
    try:
        jobs = discover_live(
            roles=roles[:1],
            locations=locations[:1] + (["remote"] if "remote" not in [l.lower() for l in locations[:1]] else []),
            extra_urls=extra_urls,
            platforms=platforms,
            headed=use_headed,
            display=display,
            slow_mo=600 if use_headed else 0,
        )
        counts = upsert_jobs(db_path=db_path, jobs=jobs)
        summary["phase_discover"] = {"jobs_found": len(jobs), **counts}
    except Exception as exc:
        summary["phase_discover"] = {"error": str(exc)}

    # ── Phase 2: Score ───────────────────────────────────────────────────────
    try:
        score_result = _stage_score(root, mode, dry_run=False, llm_client=llm_client)
        summary["phase_score"] = score_result
    except Exception as exc:
        summary["phase_score"] = {"error": str(exc)}

    # ── Phase 3: Tailor ──────────────────────────────────────────────────────
    try:
        tailor_result = _stage_tailor(
            root, mode, dry_run=False, llm_client=llm_tailor_client or llm_client
        )
        summary["phase_tailor"] = tailor_result
    except Exception as exc:
        summary["phase_tailor"] = {"error": str(exc)}

    # ── Phase 4: Apply ───────────────────────────────────────────────────────
    # Single persistent browser for the entire apply batch — stays visible in VNC.
    batch_size = int(os.getenv("HUNTBOT2_APPLY_BATCH_SIZE", "10"))
    apply_jobs = _load_tailored_jobs_for_session(db_path, batch_size)
    # Dedup by URL — same job can appear multiple times if tailored on different cycles
    _seen_apply_urls: set[str] = set()
    _deduped: list[dict] = []
    for _j in apply_jobs:
        if _j["url"] not in _seen_apply_urls:
            _seen_apply_urls.add(_j["url"])
            _deduped.append(_j)
    apply_jobs = _deduped

    if apply_jobs:
        launch_env = dict(os.environ)
        if use_headed:
            launch_env["DISPLAY"] = display

        from huntbot2.apply.applier import apply_on_page

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                profile_dir,
                headless=not use_headed,
                slow_mo=600 if use_headed else 0,
                args=[
                    "--no-sandbox",
                    "--password-store=basic",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
                env=launch_env,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport=None if use_headed else {"width": 1280, "height": 800},
                ignore_https_errors=True,
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                apply_results: list[dict] = []
                for i, job in enumerate(apply_jobs):
                    result = apply_on_page(
                        page, job,
                        submit=submit,
                        llm_client=llm_client,
                        candidate_profile=candidate_profile,
                        profile_yaml=profile_yaml,
                        master_resume=master_resume,
                        learned_answers=learned_answers,
                        project_dir=root,
                        artifact_dir=artifact_dir,
                    )
                    apply_results.append(result)
                    _update_job_status_from_result(db_path, job["id"], result)

                    if i < len(apply_jobs) - 1:
                        try:
                            page.goto(
                                "https://www.linkedin.com/jobs/",
                                timeout=15000,
                                wait_until="domcontentloaded",
                            )
                            page.wait_for_timeout(8000)
                        except Exception:
                            pass
            finally:
                ctx.close()
        summary["phase_apply"] = apply_results
    else:
        summary["phase_apply"] = []

    return summary


def _load_tailored_jobs_for_session(db_path: Path, batch_size: int) -> list[dict]:
    """Load tailored jobs from DB for the session apply phase (all platforms, not just LinkedIn)."""
    from datetime import UTC, datetime as _dt, timedelta
    cutoff = (_dt.now(UTC) - timedelta(days=14)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, company, url, score, description FROM jobs "
            "WHERE status='tailored' AND url IS NOT NULL AND TRIM(url)!='' "
            "AND updated_at >= ? "
            "ORDER BY score DESC NULLS LAST LIMIT ?",
            (cutoff, batch_size),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "title": r[1], "company": r[2], "url": r[3], "score": r[4], "description": r[5] or ""}
        for r in rows
    ]


def _update_job_status_from_result(db_path: Path, job_id: int, result: dict) -> None:
    from datetime import UTC, datetime as _dt
    status = result.get("status", "")
    now = _dt.now(UTC).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        if status == "submitted":
            conn.execute(
                "UPDATE jobs SET status='applied', updated_at=? WHERE id=?",
                (now, job_id),
            )
        elif result.get("reason") == "external_apply":
            conn.execute(
                "UPDATE jobs SET status='queued', updated_at=? WHERE id=?",
                (now, job_id),
            )
        conn.commit()
    finally:
        conn.close()
