from __future__ import annotations


def recommend_next_action(login_state: str, apply_path: str) -> dict:
    normalized_login = (login_state or "unknown").strip().lower()
    normalized_path = (apply_path or "unknown").strip().lower()

    reasons: list[str] = [f"login_state={normalized_login}", f"apply_path={normalized_path}"]

    if normalized_login == "checkpoint":
        return {
            "next_action": "pause_checkpoint",
            "route_code": "route_checkpoint",
            "reasons": reasons + ["security checkpoint detected"],
        }

    if normalized_login == "logged_out":
        return {
            "next_action": "pause_login",
            "route_code": "route_logged_out",
            "reasons": reasons + ["session not authenticated"],
        }

    if normalized_login == "logged_in" and normalized_path == "easy_apply":
        return {
            "next_action": "proceed_draft_fill",
            "route_code": "route_easy_apply",
            "reasons": reasons + ["logged in and easy-apply route available"],
        }

    if normalized_login == "logged_in" and normalized_path == "external_apply":
        return {
            "next_action": "handoff_external_apply",
            "route_code": "route_external_apply",
            "reasons": reasons + ["off-platform apply flow required"],
        }

    return {
        "next_action": "manual_review",
        "route_code": "route_manual_review",
        "reasons": reasons + ["diagnostics inconclusive or unavailable"],
    }
