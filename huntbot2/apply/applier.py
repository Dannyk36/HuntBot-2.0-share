"""Apply to a single job using Playwright with a persistent browser profile."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

_TITLE_COMMON_WORDS = frozenset({
    "manager", "senior", "associate", "analyst", "coordinator",
    "specialist", "director", "lead", "head", "chief", "vice",
    "junior", "staff", "principal", "engineer", "developer",
})

_EASY_APPLY_SELECTORS = [
    "button[aria-label*='Easy Apply']",
    ".jobs-apply-button",
    "button:has-text('Easy Apply')",
    "[data-control-name='jobdetails_topcard_inapply']",
]

_SUBMIT_SELECTORS = [
    "button[aria-label='Submit application']",
    "button:has-text('Submit application')",
    "footer button[aria-label*='Submit']",
]


def _mark_job_stale(job_id, project_dir: Path | None, reason: str) -> None:
    if project_dir is None:
        return
    from datetime import UTC, datetime as _dt
    db_path = project_dir / "data" / "huntbot2.db"
    if not db_path.exists():
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status='stale_url', updated_at=? WHERE id=?",
            (_dt.now(UTC).isoformat(), job_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def apply_on_page(
    page,
    job: dict,
    *,
    submit: bool = False,
    llm_client=None,
    candidate_profile: dict,
    profile_yaml: str = "",
    master_resume: str = "",
    learned_answers: dict | None = None,
    project_dir: Path | None = None,
    artifact_dir: Path,
) -> dict:
    """Run the apply flow on an already-open Playwright page.

    The caller owns the browser context — this function never closes it.
    Returns a result dict with status, screenshot_paths, fields_filled, etc.
    """
    from playwright.sync_api import TimeoutError as PwTimeout

    url = job.get("url", "")
    if not url:
        return {"status": "skipped", "reason": "no_url", "screenshot_paths": []}

    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    job_id = job.get("id", "unknown")
    screenshots: list[str] = []

    def snap(label: str) -> None:
        path = str(artifact_dir / f"apply-{job_id}-{label}-{int(time.time())}.png")
        try:
            page.screenshot(path=path, full_page=False)
            screenshots.append(path)
        except Exception:
            pass

    result: dict[str, Any] = {
        "job_id": job_id,
        "url": url,
        "status": "pending",
        "login_state": "unknown",
        "apply_path": "unknown",
        "next_action": "unknown",
        "fields_filled": 0,
        "submitted": False,
        "screenshot_paths": screenshots,
        "reason": None,
    }

    # Navigate to job page. Use a generous timeout; LinkedIn's React SPA can
    # be slow to fire DOMContentLoaded, especially when the session is warm.
    _partial_load = False
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
    except PwTimeout:
        # Partial load — take a screenshot and inspect whatever rendered.
        # If there's enough HTML to analyse (nav visible) we continue;
        # otherwise we bail with a hard failure.
        snap("timeout")
        html_check = page.content()
        if len(html_check) < 1000 or "<html" not in html_check.lower():
            result.update({"status": "failed", "reason": "timeout_navigate"})
            return result
        _partial_load = True

    # Wait for React to render the job content area (or an unavailable notice).
    # The shell HTML (nav + footer) loads fast; the job card is hydrated later.
    if not _partial_load:
        try:
            page.wait_for_selector(
                ".jobs-details, .jobs-apply-button, button[aria-label*='Easy Apply'], "
                ".jobs-details-top-card, [class*='job-details'], "
                "[class*='job-view-layout']",
                timeout=10000,
            )
        except PwTimeout:
            pass  # Proceed to HTML analysis regardless

    page.wait_for_timeout(500)
    snap("page_loaded")

    # Non-LinkedIn jobs skip LinkedIn-specific detection entirely.
    # The page is already loaded — hand off directly to the external apply flow.
    if "linkedin.com" not in url.lower():
        from huntbot2.apply.external_apply import apply_via_external
        ext_result = apply_via_external(
            page, job,
            llm_client=llm_client,
            candidate_profile=candidate_profile,
            profile_yaml=profile_yaml,
            master_resume=master_resume,
            learned_answers=learned_answers,
            submit=submit,
            artifact_dir=artifact_dir,
            project_dir=project_dir,
        )
        ext_result.setdefault("screenshot_paths", [])
        ext_result["screenshot_paths"] = screenshots + ext_result["screenshot_paths"]
        return ext_result

    # ── LinkedIn-only detection ───────────────────────────────────────────────
    # Full-page HTML analysis for login/availability detection.
    # Apply-path detection is refined via direct DOM inspection to avoid false
    # positives caused by other job cards in the authenticated LinkedIn left panel.
    html = page.content()
    from huntbot2.apply.linkedin_adapter import analyze_linkedin_page
    diag = analyze_linkedin_page(html=html, final_url=page.url)
    login_state = diag["login_state"]
    apply_path = diag["apply_path"]

    try:
        dom_path = page.evaluate("""() => {
            const detail = document.querySelector(
                '.jobs-details, .jobs-unified-top-card, [class*=\"job-details\"], [class*=\"job-view-layout\"]'
            );
            const scope = detail || document;
            const btn = scope.querySelector(
                'button[aria-label*=\"Easy Apply\"], button.jobs-apply-button, ' +
                'a[aria-label*=\"Apply\"], .jobs-apply-button button, .jobs-apply-button a'
            );
            if (!btn) return 'unknown_no_button';
            const label = (btn.getAttribute('aria-label') || btn.textContent || '').toLowerCase();
            const isEasyApply = label.includes('easy apply');
            const href = btn.getAttribute('href') || '';
            const isExternal = !isEasyApply && (
                btn.tagName === 'A' ||
                href.length > 0 ||
                btn.querySelector('svg[data-test-icon]') !== null ||
                label === 'apply'
            );
            if (isEasyApply) return 'easy_apply';
            if (isExternal) return 'external_apply';
            return 'unknown_button_type';
        }""")
        if dom_path and not dom_path.startswith("unknown"):
            apply_path = dom_path
    except Exception:
        pass

    result["login_state"] = login_state
    result["apply_path"] = apply_path

    if login_state == "logged_out":
        result.update({"status": "blocked", "next_action": "pause_login"})
        snap("logged_out")
        return result

    if login_state == "checkpoint":
        result.update({"status": "blocked", "next_action": "pause_checkpoint"})
        snap("checkpoint")
        return result

    if apply_path == "unavailable":
        snap("unavailable")
        result.update({"status": "skipped", "reason": "job_unavailable"})
        _mark_job_stale(job_id, project_dir, "page_unavailable")
        return result

    if login_state == "logged_in" and apply_path == "unknown":
        snap("blank_page")
        result.update({"status": "skipped", "reason": "blank_page"})
        return result

    stored_title = (job.get("title") or "").lower().strip()
    if stored_title and login_state == "logged_in":
        try:
            tab_title = (page.title() or "").lower()
            title_words = [
                w for w in stored_title.split()
                if len(w) > 3 and w not in _TITLE_COMMON_WORDS
            ]
            if title_words:
                matches = sum(1 for w in title_words if w in tab_title)
                min_matches = 2 if len(title_words) >= 2 else 1
                if matches < min_matches:
                    snap("title_mismatch")
                    result.update({"status": "skipped", "reason": "url_mismatch"})
                    _mark_job_stale(job_id, project_dir, f"title_mismatch:{tab_title[:80]!r}")
                    return result
        except Exception:
            pass

    if apply_path == "external_apply":
        from huntbot2.apply.external_apply import apply_via_external
        ext_result = apply_via_external(
            page, job,
            llm_client=llm_client,
            candidate_profile=candidate_profile,
            profile_yaml=profile_yaml,
            master_resume=master_resume,
            learned_answers=learned_answers,
            submit=submit,
            artifact_dir=artifact_dir,
            project_dir=project_dir,
        )
        # Merge screenshots captured before handoff with those from external flow
        ext_result.setdefault("screenshot_paths", [])
        ext_result["screenshot_paths"] = screenshots + ext_result["screenshot_paths"]
        return ext_result

    # Find and click Easy Apply
    clicked = False
    for sel in _EASY_APPLY_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        snap("no_easy_apply_button")
        result.update({"status": "skipped", "reason": "no_easy_apply_button"})
        return result

    # Wait for modal to appear
    try:
        page.wait_for_selector(
            ".jobs-easy-apply-content, .jobs-apply-form, [data-test-modal]",
            timeout=6000,
        )
    except PwTimeout:
        pass
    page.wait_for_timeout(1000)
    snap("modal_open")

    from huntbot2.apply.form_filler import fill_form_with_llm
    fill_result = fill_form_with_llm(
        page, job, candidate_profile, llm_client,
        profile_yaml=profile_yaml,
        master_resume=master_resume,
        learned_answers=learned_answers or {},
    )
    result["fields_filled"] = fill_result.get("fields_filled", 0)
    result["fields_skipped"] = fill_result.get("fields_skipped", 0)
    result["skipped_labels"] = fill_result.get("skipped_labels", [])

    if project_dir and fill_result.get("skipped_labels"):
        from huntbot2.apply.qa_store import add_pending_questions
        skipped_fields = [
            {"label": lbl, "type": "text"}
            for lbl in fill_result["skipped_labels"]
        ]
        add_pending_questions(project_dir, skipped_fields, job=job)

    page.wait_for_timeout(500)
    snap("form_filled")

    if submit:
        submitted = False
        for sel in _SUBMIT_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3000):
                    btn.click()
                    page.wait_for_timeout(2500)
                    snap("submitted")
                    submitted = True
                    break
            except Exception:
                continue
        result["submitted"] = submitted
        result["status"] = "submitted" if submitted else "completed"
    else:
        snap("before_submit")
        result["status"] = "completed"

    return result


def apply_to_job(
    job: dict,
    *,
    profile_dir: str,
    headless: bool = False,
    submit: bool = False,
    llm_client=None,
    candidate_profile: dict,
    profile_yaml: str = "",
    master_resume: str = "",
    learned_answers: dict | None = None,
    project_dir: Path | None = None,
    artifact_dir: Path,
) -> dict:
    """Open a fresh browser context, apply to one job, then close.

    For batch apply (multiple jobs in sequence) use apply_batch() instead so
    the browser stays open between jobs and VNC shows continuous activity.
    """
    from playwright.sync_api import sync_playwright

    url = job.get("url", "")
    if not url:
        return {"status": "skipped", "reason": "no_url", "screenshot_paths": []}

    result: dict[str, Any] = {"screenshot_paths": []}
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                profile_dir,
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--password-store=basic",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_https_errors=True,
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                result = apply_on_page(
                    page, job,
                    submit=submit,
                    llm_client=llm_client,
                    candidate_profile=candidate_profile,
                    profile_yaml=profile_yaml,
                    master_resume=master_resume,
                    learned_answers=learned_answers,
                    project_dir=project_dir,
                    artifact_dir=artifact_dir,
                )
            finally:
                ctx.close()
    except Exception as exc:
        result.update({"status": "failed", "reason": f"{type(exc).__name__}: {exc}",
                       "screenshot_paths": result.get("screenshot_paths", [])})

    return result


def apply_batch(
    jobs: list[dict],
    *,
    profile_dir: str,
    headless: bool = False,
    submit: bool = False,
    llm_client=None,
    candidate_profile: dict,
    profile_yaml: str = "",
    master_resume: str = "",
    learned_answers: dict | None = None,
    project_dir: Path | None = None,
    artifact_dir: Path,
) -> list[dict]:
    """Apply to multiple jobs in one browser session.

    Keeps the Chromium window open between jobs so VNC shows continuous
    activity.  Between jobs the browser navigates back to the LinkedIn jobs
    homepage and pauses 8 seconds to reduce bot-detection throttling.
    """
    from playwright.sync_api import sync_playwright

    if not jobs:
        return []

    results: list[dict] = []
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                profile_dir,
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--password-store=basic",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_https_errors=True,
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                for i, job in enumerate(jobs):
                    result = apply_on_page(
                        page, job,
                        submit=submit,
                        llm_client=llm_client,
                        candidate_profile=candidate_profile,
                        profile_yaml=profile_yaml,
                        master_resume=master_resume,
                        learned_answers=learned_answers,
                        project_dir=project_dir,
                        artifact_dir=artifact_dir,
                    )
                    results.append(result)
                    # Pause between jobs — navigate to jobs home then wait
                    # 8 seconds to reduce bot-detection throttling risk.
                    if i < len(jobs) - 1:
                        try:
                            page.goto("https://www.linkedin.com/jobs/",
                                      timeout=15000, wait_until="domcontentloaded")
                            page.wait_for_timeout(8000)
                        except Exception:
                            pass
            finally:
                ctx.close()
    except Exception as exc:
        if not results:
            results.append({"status": "failed",
                            "reason": f"{type(exc).__name__}: {exc}",
                            "screenshot_paths": []})

    return results
