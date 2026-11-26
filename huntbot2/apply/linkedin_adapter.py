from __future__ import annotations


def _norm(value: str) -> str:
    return (value or "").lower()


def _match_signals(text: str, markers: list[tuple[str, str]]) -> list[str]:
    return [signal for marker, signal in markers if marker in text]


def detect_login_state(html: str, final_url: str = "") -> str:
    text = _norm(html)
    url = _norm(final_url)

    checkpoint_markers = [
        ("checkpoint/challenge", "url_checkpoint_challenge"),
        ("/checkpoint/", "url_checkpoint"),
        ("security verification", "security_verification"),
        ("let's do a quick security check", "security_check"),
        ("verify your identity", "verify_identity"),
    ]
    if _match_signals(url, checkpoint_markers) or _match_signals(text, checkpoint_markers):
        return "checkpoint"

    logged_out_markers = [
        ("name=\"session_key\"", "session_key_field"),
        ("name='session_key'", "session_key_field"),
        ("name=\"session_password\"", "session_password_field"),
        ("sign in", "sign_in_text"),
        ("join now", "join_now_text"),
        ("/uas/login", "uas_login_path"),
        ("login-submit", "login_submit"),
    ]
    if _match_signals(text, logged_out_markers):
        return "logged_out"

    logged_in_markers = [
        ("global-nav", "global_nav"),
        ("me-photo", "me_photo"),
        ("feed-identity-module", "identity_module"),
        ("premium-nav", "premium_nav"),
        ("jobs-search-results-list", "jobs_results_list"),
        ("ember", "ember_component"),
    ]
    if _match_signals(text, logged_in_markers):
        return "logged_in"

    return "unknown"


def detect_apply_path(html: str, final_url: str = "") -> str:
    text = _norm(html)
    url = _norm(final_url)

    unavailable_markers = [
        ("no longer accepting applications", "no_longer_accepting"),
        ("job is no longer available", "job_no_longer_available"),
        ("this job has expired", "job_expired"),
        ("position has been filled", "position_filled"),
        # LinkedIn error page shown when job ID is invalid or posting removed
        ("unable to load the page", "linkedin_unable_to_load"),
        ("job id provided may not be valid", "linkedin_invalid_job_id"),
        ("the job posting has been", "linkedin_posting_removed"),
    ]
    if _match_signals(text, unavailable_markers):
        return "unavailable"

    easy_apply_markers = [
        (">easy apply<", "easy_apply_cta"),
        ("aria-label=\"easy apply\"", "easy_apply_aria"),
        # jobs-apply-button class is used for ALL apply buttons (Easy and external),
        # so it is NOT a reliable Easy Apply signal — omitted to avoid false positives.
        ("data-control-name=\"jobdetails_topcard_inapply\"", "easy_apply_control_name"),
    ]
    if _match_signals(text, easy_apply_markers):
        return "easy_apply"

    external_apply_markers = [
        ("apply on company website", "company_website_cta"),
        ("offsite-apply", "offsite_apply_class"),
        ("redirecting to", "redirecting_offsite"),
        ("data-control-name=\"jobdetails_topcard_inapply_external\"", "external_apply_control_name"),
        # jobs-apply-button appears on external apply too; combined with external link
        # indicators it's a reliable external-apply signal.
        ("jobs-apply-button", "apply_button_present"),
    ]
    if _match_signals(text, external_apply_markers) or "linkedin.com/jobs/view" not in url and url:
        return "external_apply"

    return "unknown"


def analyze_linkedin_page(html: str, final_url: str = "") -> dict:
    text = _norm(html)
    url = _norm(final_url)

    markers = [
        ("checkpoint/challenge", "url_checkpoint_challenge"),
        ("/checkpoint/", "url_checkpoint"),
        ("security verification", "security_verification"),
        ("verify your identity", "verify_identity"),
        ("name=\"session_key\"", "session_key_field"),
        ("name=\"session_password\"", "session_password_field"),
        ("sign in", "sign_in_text"),
        (">easy apply<", "easy_apply_cta"),
        ("aria-label=\"easy apply\"", "easy_apply_aria"),
        ("apply on company website", "company_website_cta"),
        ("offsite-apply", "offsite_apply_class"),
        ("jobs-apply-button", "apply_button_present"),
        ("no longer accepting applications", "no_longer_accepting"),
        ("unable to load the page", "linkedin_unable_to_load"),
        ("job id provided may not be valid", "linkedin_invalid_job_id"),
    ]

    signals = []
    signals.extend(_match_signals(url, markers))
    signals.extend(_match_signals(text, markers))

    if url and "linkedin.com/jobs/view" not in url:
        signals.append("url_offsite")

    return {
        "login_state": detect_login_state(html=html, final_url=final_url),
        "apply_path": detect_apply_path(html=html, final_url=final_url),
        "signals": list(dict.fromkeys(signals)),
    }
