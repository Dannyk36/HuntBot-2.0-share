from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3

from huntbot2.apply.browser_adapter import probe_job_page
from huntbot2.apply.decision import recommend_next_action
from huntbot2.apply.evidence import (
    write_apply_decision_artifact,
    write_apply_evidence,
    write_apply_probe_artifact,
    write_apply_queue_artifact,
)
from huntbot2.apply.linkedin_adapter import analyze_linkedin_page
from huntbot2.apply.queue_processor import enqueue_item
from huntbot2.apply.routing import route_decision
from huntbot2.db.schema import init_db
from huntbot2.logging.runs import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    finish_run,
    start_run,
)


@dataclass
class ApplyResult:
    status: str
    mode: str
    dry_run: bool
    submit_policy: str
    gate_decision: str
    stopped_before_submit: bool
    failure_code: str | None
    evidence_paths: list[str]
    notes: str | None
    probe_status: str | None = None
    probe_adapter_available: bool | None = None
    probe_screenshot_path: str | None = None
    decision_next_action: str | None = None
    decision_summary: str | None = None
    queue_name: str | None = None
    queue_code: str | None = None


def _select_probe_job_url(db_path: Path) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT url FROM jobs WHERE url IS NOT NULL AND TRIM(url) != '' ORDER BY score DESC NULLS LAST, id ASC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return row[0]


def _platform_of_url(url: str) -> str:
    u = url.lower()
    if "linkedin.com" in u:
        return "linkedin"
    if "indeed.com" in u:
        return "indeed"
    if "glassdoor.com" in u:
        return "glassdoor"
    if "joinhandshake.com" in u:
        return "handshake"
    return "other"


def _load_tailored_jobs(db_path: "Path") -> list[dict]:
    """Return tailored jobs for automated apply, up to 3 per platform.

    Respects HUNTBOT2_PORTALS if set — only includes enabled platforms.
    Jobs tailored more than 14 days ago are skipped (likely stale URLs).
    """
    import json as _json
    import os as _os
    from collections import defaultdict
    from datetime import UTC, datetime as _dt, timedelta

    cutoff = (_dt.now(UTC) - timedelta(days=14)).isoformat()

    portals_raw = _os.getenv("HUNTBOT2_PORTALS", "")
    enabled: set[str] | None = None
    if portals_raw:
        try:
            enabled = set(_json.loads(portals_raw))
        except Exception:
            pass

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, company, url, score, description, source FROM jobs "
            "WHERE status='tailored' AND url IS NOT NULL AND TRIM(url)!='' "
            "AND updated_at >= ? "
            "ORDER BY score DESC NULLS LAST LIMIT 60",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    per_platform: defaultdict[str, list[dict]] = defaultdict(list)
    for r in rows:
        plat = _platform_of_url(r[3] or "")
        if enabled and plat not in enabled and plat != "other":
            continue
        if len(per_platform[plat]) < 3:
            per_platform[plat].append({
                "id": r[0], "title": r[1], "company": r[2], "url": r[3],
                "score": r[4], "description": r[5] or "", "source": r[6] or plat,
                "_platform": plat,
            })

    result: list[dict] = []
    for jobs in per_platform.values():
        result.extend(jobs)
    result.sort(key=lambda j: j.get("score") or 0, reverse=True)
    return result[:10]


def _daily_apply_remaining(db_path: "Path") -> int | None:
    """Return remaining apply quota for today, or None if no cap configured."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT daily_apply_cap FROM cockpit_policy_settings WHERE policy_key='default'"
        ).fetchone()
        if not row or row[0] is None:
            return None
        cap = int(row[0])

        # Count jobs marked applied today (UTC date match)
        from datetime import datetime as _dt, UTC as _UTC
        today = _dt.now(_UTC).date().isoformat()
        applied_today = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='applied' AND date(updated_at) = ?",
            (today,),
        ).fetchone()[0]
        return max(0, cap - applied_today)
    except Exception:
        return None
    finally:
        conn.close()


def _resolve_profile_dir(platform: str, profile_base: str) -> str | None:
    """Return the browser profile directory for the given platform, or None if missing."""
    import os as _os
    candidates: list[str]
    if platform in ("linkedin", "handshake"):
        candidates = ["agent-linkedin", "linkedin"]
    elif platform == "indeed":
        candidates = ["indeed", "agent-linkedin", "linkedin"]
    elif platform == "glassdoor":
        candidates = ["glassdoor", "agent-linkedin", "linkedin"]
    else:
        candidates = ["agent-linkedin", "linkedin"]
    for name in candidates:
        d = _os.path.join(profile_base, name)
        if _os.path.exists(d):
            return d
    return None


def _run_browser_apply(
    *,
    root: "Path",
    db_path: "Path",
    mode: str,
    submit: bool,
    llm_client,
    evidence_paths: list,
    note_parts: list,
) -> list[dict]:
    """Launch Playwright apply for each tailored job. Returns list of per-job results."""
    import json as _json
    import os as _os
    from collections import defaultdict
    from datetime import UTC, datetime as _dt
    from huntbot2.apply.applier import apply_batch
    from huntbot2.apply.candidate import load_candidate_profile

    profile_base = _os.environ.get(
        "HUNTBOT2_BROWSER_PROFILE_BASE",
        _os.path.expanduser("~/.huntbot2/profiles"),
    )

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
    learned_answers = load_learned_answers(root)

    batch_size = int(_os.environ.get("HUNTBOT2_APPLY_BATCH_SIZE", "3"))

    daily_remaining = _daily_apply_remaining(db_path)
    if daily_remaining is not None and daily_remaining <= 0:
        note_parts.append("browser_apply=skipped_daily_cap_reached")
        return []
    if daily_remaining is not None:
        batch_size = min(batch_size, daily_remaining)

    all_jobs = _load_tailored_jobs(db_path)[:batch_size]

    if not all_jobs:
        note_parts.append("browser_apply=no_tailored_jobs")
        return []

    import os as _os2
    from huntbot2.discovery.live import _display_ready
    _display = _os2.getenv("HUNTBOT2_XVFB_DISPLAY", ":99")
    headless = not _display_ready(_display)  # always headed when display available
    artifact_dir = root / "artifacts"

    # Group by platform so each platform gets its own browser profile
    by_platform: defaultdict[str, list[dict]] = defaultdict(list)
    for job in all_jobs:
        by_platform[job.get("_platform", "other")].append(job)

    all_results: list[dict] = []
    conn = sqlite3.connect(db_path)
    try:
        for platform, jobs in by_platform.items():
            profile_dir = _resolve_profile_dir(platform, profile_base)
            if not profile_dir:
                note_parts.append(f"browser_apply=skipped_no_profile platform={platform}")
                continue

            # Strip internal routing key before passing to apply_batch
            clean_jobs = [{k: v for k, v in j.items() if k != "_platform"} for j in jobs]

            results = apply_batch(
                clean_jobs,
                profile_dir=profile_dir,
                headless=headless,
                submit=submit,
                llm_client=llm_client,
                candidate_profile=candidate_profile,
                profile_yaml=profile_yaml,
                master_resume=master_resume,
                learned_answers=learned_answers,
                project_dir=root,
                artifact_dir=artifact_dir,
            )
            all_results.extend(results)

            now = _dt.now(UTC).isoformat()
            for job, r in zip(clean_jobs, results):
                evidence_paths.extend(r.get("screenshot_paths", []))
                reason_part = f" reason={r['reason']}" if r.get("reason") else ""
                note_parts.append(
                    f"platform={platform} job={job['id']} apply={r['status']}{reason_part} "
                    f"screenshots={len(r.get('screenshot_paths', []))}"
                )

                for shot_path in r.get("screenshot_paths", []):
                    if _os.path.exists(shot_path):
                        parts = _os.path.basename(shot_path).split("-")
                        step = parts[3] if len(parts) > 3 else "step"
                        conn.execute(
                            "INSERT INTO artifacts (run_id, kind, path, meta_json, created_at, job_id) VALUES (?,?,?,?,?,?)",
                            (None, "apply_screenshot", shot_path,
                             _json.dumps({"job_id": job["id"], "job_title": job.get("title", ""),
                                          "job_company": job.get("company", ""), "step": step,
                                          "platform": platform}),
                             now, job["id"]),
                        )

                if r.get("submitted"):
                    conn.execute(
                        "UPDATE jobs SET status='applied', updated_at=? WHERE id=?",
                        (now, job["id"]),
                    )
                elif r.get("apply_path") == "external_apply" or r.get("reason") in (
                    "no_apply_button", "no_easy_apply_button"
                ) or str(r.get("reason", "")).startswith("ats_unsupported"):
                    # External ATS or unsupported flow — promote to manual queue
                    conn.execute(
                        "UPDATE jobs SET status='queued', updated_at=? WHERE id=?",
                        (now, job["id"]),
                    )

        conn.commit()
    finally:
        conn.close()

    return all_results


_DEFAULT_SUBMIT_TOKEN = "I_UNDERSTAND_SUBMIT"


def expected_submit_token() -> str:
    """Return the token that authorizes a live submit.

    Configurable via HUNTBOT2_SUBMIT_TOKEN so the authorizing value is not a
    hardcoded public constant. Falls back to the legacy default when unset so
    existing operators keep working.
    """
    return os.getenv("HUNTBOT2_SUBMIT_TOKEN") or _DEFAULT_SUBMIT_TOKEN


def normalize_submit_policy(policy: str | None) -> str:
    """Normalize policy aliases to canonical apply policy values."""
    raw = (policy or "").strip().lower()
    if raw in {"", "stop_before_submit", "never_submit", "safe", "manual"}:
        return "never_submit"
    if raw in {"allow_submit_with_token", "auto_submit", "submit", "go"}:
        return "allow_submit_with_token"
    return raw


def run_apply_demo(
    project_dir: str | Path,
    mode: str = "headed",
    dry_run: bool = True,
    submit_policy: str = "never_submit",
    submit_token: str | None = None,
    use_browser_probe: bool = False,
    llm_client=None,
) -> ApplyResult:
    root = Path(project_dir).resolve()
    db_path = root / "data" / "huntbot2.db"
    init_db(db_path)

    submit_policy = normalize_submit_policy(submit_policy)
    allowed_policies = {"never_submit", "allow_submit_with_token"}
    if submit_policy not in allowed_policies:
        raise ValueError(f"Unsupported submit_policy: {submit_policy}")

    token_valid = submit_token is not None and submit_token == expected_submit_token()
    if submit_policy == "allow_submit_with_token" and token_valid:
        gate_decision = "would_submit"
    elif submit_policy == "allow_submit_with_token":
        gate_decision = "policy_blocked"
    else:
        gate_decision = "stopped_before_submit"

    run = start_run(db_path, run_type="apply")
    evidence_paths = write_apply_evidence(
        root,
        run_id=run.id,
        mode=mode,
        dry_run=dry_run,
        submit_policy=submit_policy,
        token_present=submit_token is not None,
        gate_decision=gate_decision,
    )

    probe_status: str | None = None
    probe_adapter_available: bool | None = None
    probe_screenshot_path: str | None = None
    decision_next_action: str | None = None
    decision_summary: str | None = None
    queue_name: str | None = None
    queue_code: str | None = None
    note_parts: list[str] = []

    # ── Real browser apply ────────────────────────────────────────────────────
    if not dry_run and gate_decision != "policy_blocked":
        _browser_apply_results = _run_browser_apply(
            root=root,
            db_path=db_path,
            mode=mode,
            submit=(gate_decision == "would_submit"),
            llm_client=llm_client,
            evidence_paths=evidence_paths,
            note_parts=note_parts,
        )
        if _browser_apply_results:
            decision_summary = "; ".join(
                f"job={r.get('job_id', 'unknown')} status={r.get('status', 'unknown')}"
                for r in _browser_apply_results
            )

    if use_browser_probe:
        job_url = _select_probe_job_url(db_path)
        if job_url:
            run_tag = f"run-{run.id:04d}"
            probe_screenshot_path = str(root / "artifacts" / f"apply-probe-{run_tag}.png")
            probe = probe_job_page(job_url, mode=mode, screenshot_path=probe_screenshot_path)
            probe_adapter_available = bool(probe.get("adapter_available", False))
            probe_status = "completed" if probe.get("ok") else "failed"
            if not probe_adapter_available:
                probe_status = "unavailable"
                probe_screenshot_path = None
            elif not probe.get("screenshot_path"):
                probe_screenshot_path = None
            else:
                probe_screenshot_path = str(probe.get("screenshot_path"))

            evidence_paths.append(
                write_apply_probe_artifact(
                    root,
                    run_id=run.id,
                    probe_result=probe,
                    screenshot_path=probe_screenshot_path,
                )
            )

            probe_html = probe.get("html") or ""
            if probe_html:
                diagnostics = analyze_linkedin_page(
                    html=str(probe_html),
                    final_url=str(probe.get("final_url") or ""),
                )
            else:
                pseudo_html = " ".join(
                    [
                        str(probe.get("page_title") or ""),
                        " ".join(f"{k}:{v}" for k, v in (probe.get("counts") or {}).items()),
                    ]
                )
                diagnostics = analyze_linkedin_page(
                    html=pseudo_html,
                    final_url=str(probe.get("final_url") or ""),
                )

            routing = recommend_next_action(
                login_state=diagnostics["login_state"],
                apply_path=diagnostics["apply_path"],
            )
            decision_next_action = routing["next_action"]
            queue_info = route_decision(decision_next_action)
            queue_name = queue_info["queue_name"]
            queue_code = queue_info["queue_code"]
            decision_summary = (
                f"decision={routing['next_action']} "
                f"({diagnostics['login_state']}/{diagnostics['apply_path']})"
            )
            evidence_paths.append(
                write_apply_decision_artifact(
                    root,
                    run_id=run.id,
                    login_state=diagnostics["login_state"],
                    apply_path=diagnostics["apply_path"],
                    next_action=routing["next_action"],
                    reasons=routing["reasons"],
                    signals=diagnostics["signals"],
                )
            )
            evidence_paths.append(
                write_apply_queue_artifact(
                    root,
                    run_id=run.id,
                    next_action=decision_next_action,
                    queue_name=queue_name,
                    queue_code=queue_code,
                )
            )
            enqueue_item(
                db_path=db_path,
                run_id=f"run-{run.id:04d}",
                queue_name=queue_name,
                queue_code=queue_code,
                next_action=decision_next_action,
                payload={
                    "decision_context": {
                        "login_state": diagnostics["login_state"],
                        "apply_path": diagnostics["apply_path"],
                        "next_action": routing["next_action"],
                        "reasons": routing["reasons"],
                    },
                    "evidence_paths": evidence_paths,
                    "probe_screenshot_path": probe_screenshot_path,
                },
            )
            if probe_screenshot_path:
                evidence_paths.append(probe_screenshot_path)
            note_parts.append(
                f"browser_probe={probe_status} adapter_available={str(probe_adapter_available).lower()}"
            )
            if decision_summary:
                note_parts.append(decision_summary)
            if queue_name:
                note_parts.append(f"queue={queue_name}")
            if not probe_adapter_available and probe.get("reason"):
                note_parts.append(f"browser_probe_reason={probe['reason']}")
        else:
            probe_status = "skipped_no_url"
            probe_adapter_available = False
            note_parts.append("browser_probe=skipped_no_url")

    if dry_run:
        notes = "dry_run: stopped before submit"
        if note_parts:
            notes = "; ".join([notes, *note_parts])
        result = ApplyResult(
            status=RUN_STATUS_COMPLETED,
            mode=mode,
            dry_run=True,
            submit_policy=submit_policy,
            gate_decision=gate_decision,
            stopped_before_submit=True,
            failure_code=None,
            evidence_paths=evidence_paths,
            notes=notes,
            probe_status=probe_status,
            probe_adapter_available=probe_adapter_available,
            probe_screenshot_path=probe_screenshot_path,
            decision_next_action=decision_next_action,
            decision_summary=decision_summary,
            queue_name=queue_name,
            queue_code=queue_code,
        )
    elif gate_decision == "would_submit":
        notes = "Policy gate open; browser apply executed (see job=… apply=… messages for per-job outcomes)."
        if note_parts:
            notes = "; ".join([notes, *note_parts])
        result = ApplyResult(
            status=RUN_STATUS_COMPLETED,
            mode=mode,
            dry_run=False,
            submit_policy=submit_policy,
            gate_decision=gate_decision,
            stopped_before_submit=False,
            failure_code=None,
            evidence_paths=evidence_paths,
            notes=notes,
            probe_status=probe_status,
            probe_adapter_available=probe_adapter_available,
            probe_screenshot_path=probe_screenshot_path,
            decision_next_action=decision_next_action,
            decision_summary=decision_summary,
            queue_name=queue_name,
            queue_code=queue_code,
        )
    else:
        notes = "Submission blocked by policy. Add explicit policy gate override before live submit."
        if note_parts:
            notes = "; ".join([notes, *note_parts])
        result = ApplyResult(
            status=RUN_STATUS_FAILED,
            mode=mode,
            dry_run=False,
            submit_policy=submit_policy,
            gate_decision=gate_decision,
            stopped_before_submit=True,
            failure_code="policy_blocked",
            evidence_paths=evidence_paths,
            notes=notes,
            probe_status=probe_status,
            probe_adapter_available=probe_adapter_available,
            probe_screenshot_path=probe_screenshot_path,
            decision_next_action=decision_next_action,
            decision_summary=decision_summary,
            queue_name=queue_name,
            queue_code=queue_code,
        )

    finish_run(
        db_path,
        run.id,
        status=result.status,
        failure_code=result.failure_code,
        notes=result.notes,
    )

    return result
