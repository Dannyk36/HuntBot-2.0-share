"""Handle external (non-Easy-Apply) ATS application flows.

When a LinkedIn job uses an external Apply button, this module:
1. Clicks the button, handling popup tabs or in-page navigation
2. Detects the ATS platform from the landing URL
3. Fills every form page using the same LLM form-filler
4. Submits when allowed by policy
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# ATS families that require account creation or are too complex to automate —
# these get routed to the manual (queued) queue instead.
_SKIP_ATS = frozenset({"taleo", "icims", "successfactors", "paycom"})

_ATS_PATTERNS = [
    ("myworkday", "workday"),
    ("boards.greenhouse.io", "greenhouse"),
    ("jobs.lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("bamboohr.com", "bamboohr"),
    ("recruitingbypaycor", "paycor"),
    ("paylocity.com", "paylocity"),
    ("jobvite.com", "jobvite"),
    ("workable.com", "workable"),
    ("rippling.com", "rippling"),
    ("icims.com", "icims"),
    ("taleo.net", "taleo"),
    ("successfactors", "successfactors"),
    ("paycom", "paycom"),
]

# Selectors that advance multi-page ATS forms
_NEXT_SELECTORS = [
    "button:has-text('Next')",
    "button:has-text('Continue')",
    "button[aria-label*='Next']",
    "[data-automation-id='bottom-navigation-next-button']",  # Workday
    "button[data-ui='next-button']",
]

# Final submit selectors (tried after all Next pages exhausted)
_SUBMIT_SELECTORS = [
    "button:has-text('Submit application')",
    "button:has-text('Submit Application')",
    "button:has-text('Submit')",
    "button:has-text('Apply')",
    "input[type='submit']",
    "button[type='submit']",
    "button[data-ui='save-and-submit']",
    "[data-automation-id='bottom-navigation-next-button']",  # Workday final step
    "button[aria-label*='Submit']",
]


def detect_ats(url: str) -> str:
    u = url.lower()
    for pattern, name in _ATS_PATTERNS:
        if pattern in u:
            return name
    return "generic"


def _fill_and_advance(
    ats_page,
    job: dict,
    candidate_profile: dict,
    llm_client,
    profile_yaml: str,
    master_resume: str,
    learned_answers: dict,
    max_pages: int = 8,
) -> tuple[int, list[str]]:
    """Fill each form page and click Next/Continue until no more advance buttons.

    Returns (total_fields_filled, all_skipped_labels).
    """
    from huntbot2.apply.form_filler import fill_form_with_llm
    from playwright.sync_api import TimeoutError as PwTimeout

    total_filled = 0
    all_skipped: list[str] = []

    for _ in range(max_pages):
        ats_page.wait_for_timeout(1500)

        fill_result = fill_form_with_llm(
            ats_page, job, candidate_profile, llm_client,
            profile_yaml=profile_yaml,
            master_resume=master_resume,
            learned_answers=learned_answers,
        )
        total_filled += fill_result.get("fields_filled", 0)
        all_skipped.extend(fill_result.get("skipped_labels", []))

        ats_page.wait_for_timeout(500)

        advanced = False
        for sel in _NEXT_SELECTORS:
            try:
                btn = ats_page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    try:
                        ats_page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except PwTimeout:
                        pass
                    advanced = True
                    break
            except Exception:
                continue

        if not advanced:
            break

    return total_filled, all_skipped


def apply_via_external(
    page,
    job: dict,
    *,
    llm_client=None,
    candidate_profile: dict,
    profile_yaml: str = "",
    master_resume: str = "",
    learned_answers: dict | None = None,
    submit: bool = False,
    artifact_dir: Path,
    project_dir: Path | None = None,
) -> dict:
    """Follow an external Apply link from a LinkedIn job page and complete the ATS form.

    The caller still owns the browser context; this function never closes it.
    Returns a result dict with the same shape as apply_on_page().
    """
    from playwright.sync_api import TimeoutError as PwTimeout

    job_id = job.get("id", "unknown")
    artifact_dir = Path(artifact_dir)
    screenshots: list[str] = []

    def snap(label: str) -> None:
        path = str(artifact_dir / f"apply-{job_id}-ext-{label}-{int(time.time())}.png")
        try:
            page.screenshot(path=path, full_page=False)
            screenshots.append(path)
        except Exception:
            pass

    result: dict[str, Any] = {
        "job_id": job_id,
        "url": job.get("url", ""),
        "status": "pending",
        "apply_path": "external_apply",
        "login_state": "logged_in",
        "next_action": "external_apply",
        "fields_filled": 0,
        "submitted": False,
        "screenshot_paths": screenshots,
        "reason": None,
    }

    # ── Click the Apply button ────────────────────────────────────────────────
    # Works for LinkedIn external apply buttons (navigates off-site to ATS) and
    # for Indeed/Glassdoor job pages where the page is already loaded.
    apply_selectors = [
        # LinkedIn
        "a[data-control-name*='apply']",
        ".jobs-apply-button a",
        ".jobs-apply-button button",
        # Indeed
        "button[data-testid='indeedApplyButton']",
        ".ia-IndeedApplyButton",
        "#indeedApplyButton",
        # Glassdoor
        "button[data-test='applyButton']",
        "button[data-brandingcontext='jd-header-app-btn']",
        # Generic
        "button[aria-label*='Apply']",
        "a[aria-label*='Apply']",
        "button:has-text('Apply now')",
        "a:has-text('Apply now')",
        "button:has-text('Easy Apply')",
        "button:has-text('Apply')",
        "a:has-text('Apply')",
    ]

    ats_page = None

    for sel in apply_selectors:
        try:
            btn = page.locator(sel).first
            if not btn.is_visible(timeout=2000):
                continue
            # Try popup first (new tab)
            try:
                with page.context.expect_page(timeout=4000) as popup_info:
                    btn.click()
                ats_page = popup_info.value
                ats_page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                # No popup — navigated current page to external site
                btn.click()
                try:
                    page.wait_for_url(
                        lambda u: "linkedin.com" not in u.lower(),
                        timeout=8000,
                    )
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                ats_page = page
            break
        except Exception:
            continue

    if ats_page is None:
        result.update({"status": "skipped", "reason": "no_apply_button"})
        snap("no_apply_button")
        return result

    ats_page.wait_for_timeout(2000)
    snap("ats_loaded")

    ats_type = detect_ats(ats_page.url)
    result["ats_type"] = ats_type

    if ats_type in _SKIP_ATS:
        result.update({"status": "skipped", "reason": f"ats_unsupported:{ats_type}"})
        snap("ats_unsupported")
        if ats_page is not page:
            try:
                ats_page.close()
            except Exception:
                pass
        return result

    # ── Fill form page(s) ────────────────────────────────────────────────────
    total_filled, skipped_labels = _fill_and_advance(
        ats_page, job, candidate_profile, llm_client,
        profile_yaml, master_resume, learned_answers or {},
    )
    result["fields_filled"] = total_filled
    result["skipped_labels"] = skipped_labels

    snap("form_filled")

    # ── Submit ───────────────────────────────────────────────────────────────
    if submit:
        submitted = False
        for sel in _SUBMIT_SELECTORS:
            try:
                btn = ats_page.locator(sel).first
                if btn.is_visible(timeout=3000):
                    btn.click()
                    ats_page.wait_for_timeout(3000)
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

    # Close the ATS tab if it was opened as a popup
    if ats_page is not page:
        try:
            ats_page.close()
        except Exception:
            pass

    return result
