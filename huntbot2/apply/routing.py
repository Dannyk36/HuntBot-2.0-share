ROUTE_MAP = {
    "proceed_draft_fill": {
        "queue_name": "draft_fill_queue",
        "queue_code": "Q_DRAFT_FILL",
    },
    "pause_login": {
        "queue_name": "login_pause_queue",
        "queue_code": "Q_LOGIN_PAUSE",
    },
    "pause_checkpoint": {
        "queue_name": "checkpoint_queue",
        "queue_code": "Q_CHECKPOINT",
    },
    "handoff_external_apply": {
        "queue_name": "external_handoff_queue",
        "queue_code": "Q_EXTERNAL_HANDOFF",
    },
    "manual_review": {
        "queue_name": "manual_review_queue",
        "queue_code": "Q_MANUAL_REVIEW",
    },
}


def route_decision(next_action: str) -> dict[str, str]:
    if next_action not in ROUTE_MAP:
        raise ValueError(f"Unsupported next_action for routing: {next_action}")
    return dict(ROUTE_MAP[next_action])
