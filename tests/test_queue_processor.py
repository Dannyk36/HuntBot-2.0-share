import json
from pathlib import Path

from huntbot2.apply.queue_processor import enqueue_item, fetch_pending, process_item, queue_snapshot_counts


def test_enqueue_fetch_and_process_resolved(tmp_path: Path):
    db_path = tmp_path / "data" / "huntbot2.db"
    item_id = enqueue_item(
        db_path=db_path,
        run_id="run-0001",
        queue_name="draft_fill_queue",
        queue_code="Q_DRAFT_FILL",
        next_action="proceed_draft_fill",
        payload={"decision_context": {"login_state": "logged_in"}, "evidence_paths": ["/tmp/a.json"]},
    )

    pending = fetch_pending(db_path=db_path, limit=10)
    assert len(pending) == 1
    assert pending[0]["id"] == item_id
    assert json.loads(pending[0]["payload_json"])["decision_context"]["login_state"] == "logged_in"

    result = process_item(db_path=db_path, item_id=item_id)
    assert result["status"] == "resolved"
    assert result["attempts"] == 1
    assert result["note"] == "ready_for_draft_fill"

    snapshot = queue_snapshot_counts(db_path)
    assert snapshot["resolved"] == 1
    assert snapshot["pending"] == 0


def test_process_item_blocked_routes(tmp_path: Path):
    db_path = tmp_path / "data" / "huntbot2.db"
    login_id = enqueue_item(
        db_path=db_path,
        run_id="run-0002",
        queue_name="login_pause_queue",
        queue_code="Q_LOGIN_PAUSE",
        next_action="pause_login",
        payload={"decision_context": {}, "evidence_paths": []},
    )
    manual_id = enqueue_item(
        db_path=db_path,
        run_id="run-0003",
        queue_name="manual_review_queue",
        queue_code="Q_MANUAL_REVIEW",
        next_action="manual_review",
        payload={"decision_context": {}, "evidence_paths": []},
    )

    r1 = process_item(db_path=db_path, item_id=login_id)
    r2 = process_item(db_path=db_path, item_id=manual_id)

    assert r1["status"] == "blocked"
    assert r1["note"] == "requires_login"
    assert r2["status"] == "blocked"
    assert r2["note"] == "requires_manual_review"

    snapshot = queue_snapshot_counts(db_path)
    assert snapshot["blocked"] == 2
