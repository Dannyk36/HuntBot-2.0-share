from huntbot2.apply.decision import recommend_next_action


def test_recommend_checkpoint_routes_to_pause_checkpoint():
    result = recommend_next_action("checkpoint", "easy_apply")
    assert result["next_action"] == "pause_checkpoint"
    assert result["route_code"] == "route_checkpoint"


def test_recommend_logged_out_routes_to_pause_login():
    result = recommend_next_action("logged_out", "external_apply")
    assert result["next_action"] == "pause_login"


def test_recommend_logged_in_easy_apply_routes_to_proceed_draft_fill():
    result = recommend_next_action("logged_in", "easy_apply")
    assert result["next_action"] == "proceed_draft_fill"


def test_recommend_logged_in_external_apply_routes_to_handoff_external_apply():
    result = recommend_next_action("logged_in", "external_apply")
    assert result["next_action"] == "handoff_external_apply"


def test_recommend_unknown_routes_to_manual_review():
    result = recommend_next_action("unknown", "unknown")
    assert result["next_action"] == "manual_review"
    assert result["route_code"] == "route_manual_review"
