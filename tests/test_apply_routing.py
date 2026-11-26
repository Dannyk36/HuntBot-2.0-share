import pytest

from huntbot2.apply.routing import route_decision


@pytest.mark.parametrize(
    ("next_action", "queue_name", "queue_code"),
    [
        ("proceed_draft_fill", "draft_fill_queue", "Q_DRAFT_FILL"),
        ("pause_login", "login_pause_queue", "Q_LOGIN_PAUSE"),
        ("pause_checkpoint", "checkpoint_queue", "Q_CHECKPOINT"),
        ("handoff_external_apply", "external_handoff_queue", "Q_EXTERNAL_HANDOFF"),
        ("manual_review", "manual_review_queue", "Q_MANUAL_REVIEW"),
    ],
)
def test_route_decision_maps_all_actions(next_action: str, queue_name: str, queue_code: str):
    routed = route_decision(next_action)
    assert routed == {"queue_name": queue_name, "queue_code": queue_code}


def test_route_decision_rejects_unknown_action():
    with pytest.raises(ValueError):
        route_decision("unknown_action")
