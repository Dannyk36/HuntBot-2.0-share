from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from urllib import request


ALERT_SEVERITIES = {"info", "warning", "critical"}
ALERT_STATUSES = {"new", "acked", "resolved"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def configured_channels() -> list[str]:
    configured = os.getenv("HUNTBOT2_ALERT_CHANNELS", "in_app,webhook")
    channels = [item.strip().lower() for item in configured.split(",") if item.strip()]
    return channels or ["in_app"]


def should_send_webhook() -> bool:
    return "webhook" in configured_channels() and bool(os.getenv("HUNTBOT2_ALERT_WEBHOOK_URL"))


def build_webhook_payload(alert: dict[str, object]) -> dict[str, object]:
    if _truthy(os.getenv("HUNTBOT2_ALERT_WEBHOOK_TELEGRAM_FORMAT")):
        return {
            "text": (
                f"[{str(alert['severity']).upper()}] {alert['alert_type']}\n"
                f"{alert['message']}\n"
                f"status={alert['status']} count={alert['occurrence_count']}"
            )
        }
    return {
        "kind": "huntbot_alert",
        "sent_at": _now_iso(),
        "alert": alert,
    }


def send_webhook_notification(alert: dict[str, object]) -> dict[str, object]:
    url = os.getenv("HUNTBOT2_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return {"sent": False, "reason": "no_webhook_url"}

    payload = build_webhook_payload(alert)
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url=url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with request.urlopen(req, timeout=5) as response:  # noqa: S310
            status_code = int(response.status)
        return {"sent": 200 <= status_code < 300, "status_code": status_code}
    except Exception as exc:  # pragma: no cover
        return {"sent": False, "reason": str(exc)}
