from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3

from huntbot2.apply.queue_processor import queue_snapshot_counts
from huntbot2.db.schema import init_db

PLATFORMS = ("linkedin", "indeed", "glassdoor", "handshake")
POLICY_KEY_DEFAULT = "default"
SESSION_WORKFLOW_STATES = ("request_takeover", "in_takeover", "resumed", "paused")
TAKEOVER_REASON_CODES = ("login_required", "captcha", "mfa", "manual_review")
RESUME_STEPS = ("open_job", "validate_page", "fill_form", "review", "submit_gate")
RISKY_RESUME_STEPS = {"review", "submit_gate"}
PACING_PRESETS = ("safe", "balanced", "aggressive")
REMEDIATION_PLATFORM_GLOBAL = "*"
REMEDIATION_ALLOWED_ACTIONS = (
    "pause_scheduler",
    "force_checkpoint_required",
    "downgrade_pacing_safe",
    "open_takeover_request",
)
PACING_PRESET_CONFIG: dict[str, dict[str, object]] = {
    "safe": {
        "min_seconds_multiplier": 1.5,
        "daily_cap_multiplier": 0.6,
        "checkpoint_strictness_hint": "strict",
    },
    "balanced": {
        "min_seconds_multiplier": 1.0,
        "daily_cap_multiplier": 1.0,
        "checkpoint_strictness_hint": "standard",
    },
    "aggressive": {
        "min_seconds_multiplier": 0.7,
        "daily_cap_multiplier": 1.4,
        "checkpoint_strictness_hint": "relaxed",
    },
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_profile_path(platform: str) -> str:
    base = os.getenv("HUNTBOT2_BROWSER_PROFILE_BASE", str(Path.home() / ".huntbot2" / "profiles"))
    return str(Path(base) / platform)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class GuardrailPolicy:
    def __init__(self, repo: "CockpitRepository", policy_key: str = POLICY_KEY_DEFAULT):
        self.repo = repo
        self.policy_key = policy_key

    def evaluate(self, event_type: str, requires_submit: bool = False) -> dict[str, object]:
        settings = self.repo.get_policy_settings(policy_key=self.policy_key)
        status = self.repo.policy_status(policy_key=self.policy_key)
        violations: list[str] = []

        if event_type == "apply" and status["today_apply_count"] >= settings["daily_apply_cap"]:
            violations.append("daily_apply_cap_reached")

        if status["seconds_since_last_action"] is not None:
            if float(status["seconds_since_last_action"]) < settings["min_seconds_between_actions"]:
                violations.append("min_seconds_between_actions")

        if requires_submit and settings["require_human_checkpoint"] and not status["checkpoint_valid"]:
            violations.append("human_checkpoint_required")

        return {
            "allowed": not violations,
            "violations": violations,
            "policy": settings,
            "status": status,
            "event_type": event_type,
            "requires_submit": requires_submit,
        }


class CockpitRepository:
    """Tiny SQLite repository for cockpit state and queue snapshot access."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        init_db(self.db_path)

    def queue_snapshot(self) -> dict[str, int]:
        return queue_snapshot_counts(self.db_path)

    def get_takeover_flag(self, session_key: str = "default") -> dict[str, object]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT session_key, takeover_requested, checkpoint_required, updated_at
                FROM cockpit_takeover_flags
                WHERE session_key = ?
                """,
                (session_key,),
            ).fetchone()
            if row is None:
                now = _now_iso()
                conn.execute(
                    """
                    INSERT INTO cockpit_takeover_flags (session_key, takeover_requested, checkpoint_required, updated_at)
                    VALUES (?, 0, 1, ?)
                    """,
                    (session_key, now),
                )
                conn.commit()
                return {
                    "session_key": session_key,
                    "takeover_requested": False,
                    "checkpoint_required": True,
                    "updated_at": now,
                }

            return {
                "session_key": str(row[0]),
                "takeover_requested": bool(row[1]),
                "checkpoint_required": bool(row[2]),
                "updated_at": str(row[3]),
            }

    def set_takeover_flag(self, requested: bool, session_key: str = "default") -> dict[str, object]:
        now = _now_iso()
        checkpoint_required = bool(requested)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_takeover_flags (session_key, takeover_requested, checkpoint_required, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                  takeover_requested = excluded.takeover_requested,
                  checkpoint_required = excluded.checkpoint_required,
                  updated_at = excluded.updated_at
                """,
                (session_key, int(requested), int(checkpoint_required), now),
            )
            conn.commit()
        return self.get_takeover_flag(session_key=session_key)

    def _ensure_platform_rows(self) -> None:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            for platform in PLATFORMS:
                conn.execute(
                    """
                    INSERT INTO cockpit_browser_profiles (platform, profile_path, active, status, updated_at)
                    VALUES (?, ?, 0, 'idle', ?)
                    ON CONFLICT(platform) DO NOTHING
                    """,
                    (platform, _default_profile_path(platform), now),
                )
            conn.commit()

    def _ensure_session_workflow_rows(self, session_key: str = "default") -> None:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            for platform in PLATFORMS:
                conn.execute(
                    """
                    INSERT INTO cockpit_session_workflows (platform, session_key, state, updated_at)
                    VALUES (?, ?, 'paused', ?)
                    ON CONFLICT(platform, session_key) DO NOTHING
                    """,
                    (platform, session_key, now),
                )
            conn.commit()

    def list_session_workflows(self, session_key: str = "default") -> list[dict[str, object]]:
        self._ensure_session_workflow_rows(session_key=session_key)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT platform, session_key, state, reason_code, note, updated_at
                FROM cockpit_session_workflows
                WHERE session_key = ?
                ORDER BY platform
                """,
                (session_key,),
            ).fetchall()

        return [
            {
                "platform": str(row[0]),
                "session_key": str(row[1]),
                "state": str(row[2]),
                "reason_code": row[3],
                "note": row[4],
                "updated_at": str(row[5]),
            }
            for row in rows
        ]

    def get_session_workflow(self, platform: str, session_key: str = "default") -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        self._ensure_session_workflow_rows(session_key=session_key)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT platform, session_key, state, reason_code, note, updated_at
                FROM cockpit_session_workflows
                WHERE platform = ? AND session_key = ?
                """,
                (platform, session_key),
            ).fetchone()
        if row is None:
            raise ValueError("session workflow missing")
        return {
            "platform": str(row[0]),
            "session_key": str(row[1]),
            "state": str(row[2]),
            "reason_code": row[3],
            "note": row[4],
            "updated_at": str(row[5]),
        }

    def update_session_workflow(
        self,
        platform: str,
        session_key: str = "default",
        state: str | None = None,
        reason_code: str | None = None,
        note: str | None = None,
    ) -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        if state is not None and state not in SESSION_WORKFLOW_STATES:
            raise ValueError(f"unsupported state: {state}")
        if reason_code is not None and reason_code not in TAKEOVER_REASON_CODES:
            raise ValueError(f"unsupported reason_code: {reason_code}")

        current = self.get_session_workflow(platform=platform, session_key=session_key)
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_session_workflows (platform, session_key, state, reason_code, note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, session_key) DO UPDATE SET
                  state = excluded.state,
                  reason_code = excluded.reason_code,
                  note = excluded.note,
                  updated_at = excluded.updated_at
                """,
                (
                    platform,
                    session_key,
                    state if state is not None else str(current["state"]),
                    reason_code if reason_code is not None else current["reason_code"],
                    note if note is not None else current["note"],
                    now,
                ),
            )
            conn.commit()

        return self.get_session_workflow(platform=platform, session_key=session_key)

    def request_takeover(
        self,
        platform: str,
        session_key: str = "default",
        reason_code: str | None = None,
        note: str | None = None,
    ) -> dict[str, object]:
        return self.update_session_workflow(
            platform=platform,
            session_key=session_key,
            state="request_takeover",
            reason_code=reason_code,
            note=note,
        )

    def _ensure_resume_step_rows(self, session_key: str = "default") -> None:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            for platform in PLATFORMS:
                conn.execute(
                    """
                    INSERT INTO cockpit_resume_steps (platform, session_key, resume_step, updated_at)
                    VALUES (?, ?, 'open_job', ?)
                    ON CONFLICT(platform, session_key) DO NOTHING
                    """,
                    (platform, session_key, now),
                )
            conn.commit()

    def get_resume_step(self, platform: str, session_key: str = "default") -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        self._ensure_resume_step_rows(session_key=session_key)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT platform, session_key, resume_step, updated_by, note, updated_at
                FROM cockpit_resume_steps
                WHERE platform = ? AND session_key = ?
                """,
                (platform, session_key),
            ).fetchone()
        if row is None:
            raise ValueError("resume step missing")
        return {
            "platform": str(row[0]),
            "session_key": str(row[1]),
            "resume_step": str(row[2]),
            "updated_by": row[3],
            "note": row[4],
            "updated_at": str(row[5]),
        }

    def set_resume_step(
        self,
        platform: str,
        session_key: str,
        resume_step: str,
        updated_by: str | None = None,
        note: str | None = None,
    ) -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        if resume_step not in RESUME_STEPS:
            raise ValueError(f"unsupported resume_step: {resume_step}")
        self._ensure_resume_step_rows(session_key=session_key)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_resume_steps (platform, session_key, resume_step, updated_by, note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, session_key) DO UPDATE SET
                  resume_step = excluded.resume_step,
                  updated_by = excluded.updated_by,
                  note = excluded.note,
                  updated_at = excluded.updated_at
                """,
                (platform, session_key, resume_step, updated_by, note, _now_iso()),
            )
            conn.commit()
        return self.get_resume_step(platform=platform, session_key=session_key)

    def _ensure_scheduler_rows(self) -> None:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            for platform in PLATFORMS:
                conn.execute(
                    """
                    INSERT INTO cockpit_scheduler_state (platform, paused, window_start, window_end, timezone, updated_at)
                    VALUES (?, 0, '08:00', '20:00', 'UTC', ?)
                    ON CONFLICT(platform) DO NOTHING
                    """,
                    (platform, now),
                )
            conn.commit()

    def list_scheduler_state(self) -> list[dict[str, object]]:
        self._ensure_scheduler_rows()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT platform, paused, window_start, window_end, timezone, note, updated_at
                FROM cockpit_scheduler_state
                ORDER BY platform
                """
            ).fetchall()
        return [
            {
                "platform": str(row[0]),
                "paused": bool(row[1]),
                "window_start": str(row[2]),
                "window_end": str(row[3]),
                "timezone": str(row[4]),
                "note": row[5],
                "updated_at": str(row[6]),
            }
            for row in rows
        ]

    def update_scheduler_state(
        self,
        platform: str,
        paused: bool | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
        timezone: str | None = None,
        note: str | None = None,
    ) -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        self._ensure_scheduler_rows()
        with sqlite3.connect(self.db_path) as conn:
            current = conn.execute(
                """
                SELECT paused, window_start, window_end, timezone, note
                FROM cockpit_scheduler_state
                WHERE platform = ?
                """,
                (platform,),
            ).fetchone()
            assert current is not None
            conn.execute(
                """
                INSERT INTO cockpit_scheduler_state (platform, paused, window_start, window_end, timezone, note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                  paused = excluded.paused,
                  window_start = excluded.window_start,
                  window_end = excluded.window_end,
                  timezone = excluded.timezone,
                  note = excluded.note,
                  updated_at = excluded.updated_at
                """,
                (
                    platform,
                    int(paused if paused is not None else bool(current[0])),
                    window_start if window_start is not None else str(current[1]),
                    window_end if window_end is not None else str(current[2]),
                    timezone if timezone is not None else str(current[3]),
                    note if note is not None else current[4],
                    _now_iso(),
                ),
            )
            conn.commit()
        return next(item for item in self.list_scheduler_state() if item["platform"] == platform)

    def record_operator_event(
        self,
        event_type: str,
        actor: str,
        platform: str | None = None,
        session_key: str | None = None,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_operator_timeline (event_type, actor, platform, session_key, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_type, actor, platform, session_key, json.dumps(details or {}), _now_iso()),
            )
            event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
        return self.list_operator_events(limit=1, event_id=int(event_id))[0]

    def list_operator_events(
        self,
        limit: int = 50,
        event_type: str | None = None,
        platform: str | None = None,
        session_key: str | None = None,
        actor: str | None = None,
        event_id: int | None = None,
    ) -> list[dict[str, object]]:
        query = [
            """
            SELECT id, event_type, actor, platform, session_key, details_json, created_at
            FROM cockpit_operator_timeline
            WHERE 1=1
            """
        ]
        params: list[object] = []
        if event_type:
            query.append("AND event_type = ?")
            params.append(event_type)
        if platform:
            query.append("AND platform = ?")
            params.append(platform)
        if session_key:
            query.append("AND session_key = ?")
            params.append(session_key)
        if actor:
            query.append("AND actor = ?")
            params.append(actor)
        if event_id is not None:
            query.append("AND id = ?")
            params.append(event_id)
        query.append("ORDER BY id DESC LIMIT ?")
        params.append(max(1, min(limit, 200)))

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("\n".join(query), params).fetchall()

        events: list[dict[str, object]] = []
        for row in rows:
            details: dict[str, object] = {}
            try:
                details = json.loads(str(row[5] or "{}"))
            except json.JSONDecodeError:
                details = {"raw": row[5]}
            events.append(
                {
                    "id": int(row[0]),
                    "event_type": str(row[1]),
                    "actor": str(row[2]),
                    "platform": row[3],
                    "session_key": row[4],
                    "details": details,
                    "created_at": str(row[6]),
                }
            )
        return events

    def _ensure_platform_pacing_rows(self) -> None:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            for platform in PLATFORMS:
                conn.execute(
                    """
                    INSERT INTO cockpit_platform_pacing (platform, preset, updated_at)
                    VALUES (?, 'balanced', ?)
                    ON CONFLICT(platform) DO NOTHING
                    """,
                    (platform, now),
                )
            conn.commit()

    def _map_preset_to_policy_knobs(self, preset: str) -> dict[str, object]:
        if preset not in PACING_PRESET_CONFIG:
            raise ValueError(f"unsupported pacing preset: {preset}")
        base = self.get_policy_settings()
        cfg = PACING_PRESET_CONFIG[preset]
        min_seconds = max(0, int(round(base["min_seconds_between_actions"] * float(cfg["min_seconds_multiplier"]))))
        daily_cap = max(1, int(round(base["daily_apply_cap"] * float(cfg["daily_cap_multiplier"]))))
        return {
            "min_seconds_between_actions": min_seconds,
            "daily_apply_cap": daily_cap,
            "checkpoint_strictness_hint": cfg["checkpoint_strictness_hint"],
        }

    def get_platform_pacing(self, platform: str) -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        self._ensure_platform_pacing_rows()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT platform, preset, updated_by, note, updated_at FROM cockpit_platform_pacing WHERE platform = ?",
                (platform,),
            ).fetchone()
        if row is None:
            raise ValueError("platform pacing missing")
        preset = str(row[1])
        return {
            "platform": str(row[0]),
            "preset": preset,
            "preset_options": list(PACING_PRESETS),
            "policy_knobs": self._map_preset_to_policy_knobs(preset),
            "updated_by": row[2],
            "note": row[3],
            "updated_at": str(row[4]),
        }

    def list_platform_pacing(self) -> list[dict[str, object]]:
        self._ensure_platform_pacing_rows()
        return [self.get_platform_pacing(platform) for platform in PLATFORMS]

    def set_platform_pacing(self, platform: str, preset: str, updated_by: str | None = None, note: str | None = None) -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        if preset not in PACING_PRESETS:
            raise ValueError(f"unsupported pacing preset: {preset}")
        self._ensure_platform_pacing_rows()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_platform_pacing (platform, preset, updated_by, note, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                  preset = excluded.preset,
                  updated_by = excluded.updated_by,
                  note = excluded.note,
                  updated_at = excluded.updated_at
                """,
                (platform, preset, updated_by, note, _now_iso()),
            )
            conn.commit()
        return self.get_platform_pacing(platform)

    def list_browser_profiles(self) -> list[dict[str, object]]:
        self._ensure_platform_rows()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT platform, profile_path, active, status, last_used_at, note, updated_at
                FROM cockpit_browser_profiles
                ORDER BY platform
                """
            ).fetchall()

        profiles = [
            {
                "platform": str(row[0]),
                "profile_path": str(row[1]),
                "active": bool(row[2]),
                "status": str(row[3]),
                "last_used_at": row[4],
                "note": row[5],
                "updated_at": str(row[6]),
            }
            for row in rows
        ]

        health = self.session_health_summary()
        by_platform = {item["platform"]: item for item in health["platforms"]}
        for profile in profiles:
            profile["health"] = by_platform.get(profile["platform"], {"warnings": [], "severity": "ok"})
        return profiles

    def upsert_browser_profile(
        self,
        platform: str,
        profile_path: str | None = None,
        active: bool | None = None,
        status: str | None = None,
        note: str | None = None,
        last_used_at: str | None = None,
    ) -> dict[str, object]:
        if platform not in PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")

        self._ensure_platform_rows()
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            current = conn.execute(
                """
                SELECT profile_path, active, status, last_used_at, note
                FROM cockpit_browser_profiles
                WHERE platform = ?
                """,
                (platform,),
            ).fetchone()
            if current is None:
                current = (_default_profile_path(platform), 0, "idle", None, None)

            conn.execute(
                """
                INSERT INTO cockpit_browser_profiles (platform, profile_path, active, status, last_used_at, note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                  profile_path = excluded.profile_path,
                  active = excluded.active,
                  status = excluded.status,
                  last_used_at = excluded.last_used_at,
                  note = excluded.note,
                  updated_at = excluded.updated_at
                """,
                (
                    platform,
                    profile_path if profile_path is not None else str(current[0]),
                    int(active if active is not None else bool(current[1])),
                    status if status is not None else str(current[2]),
                    last_used_at if last_used_at is not None else current[3],
                    note if note is not None else current[4],
                    now,
                ),
            )
            conn.commit()

        return next(p for p in self.list_browser_profiles() if p["platform"] == platform)

    def session_health_summary(self) -> dict[str, object]:
        self._ensure_platform_rows()
        now = datetime.now(UTC)
        stale_threshold_seconds = 12 * 3600
        with sqlite3.connect(self.db_path) as conn:
            profiles = conn.execute(
                """
                SELECT platform, active, status, last_used_at
                FROM cockpit_browser_profiles
                ORDER BY platform
                """
            ).fetchall()
            failed_checkpoints = conn.execute(
                """
                SELECT COUNT(*)
                FROM cockpit_policy_events
                WHERE event_type = 'checkpoint_failed'
                  AND created_at >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]

        items: list[dict[str, object]] = []
        for row in profiles:
            platform = str(row[0])
            active = bool(row[1])
            status = str(row[2])
            last_used_at = row[3]
            warnings: list[dict[str, str]] = []

            if not active or status in {"inactive", "disabled"}:
                warnings.append({"code": "profile_inactive", "message": "Browser profile is inactive"})

            parsed = _parse_iso(last_used_at)
            if parsed is None:
                warnings.append({"code": "missing_last_used_at", "message": "No last_used_at recorded"})
            else:
                age_seconds = (now - parsed).total_seconds()
                if age_seconds > stale_threshold_seconds:
                    warnings.append(
                        {
                            "code": "stale_last_used_at",
                            "message": f"Last used {int(age_seconds // 3600)}h ago",
                        }
                    )

            if failed_checkpoints >= 3:
                warnings.append(
                    {
                        "code": "frequent_checkpoint_failures",
                        "message": f"{int(failed_checkpoints)} checkpoint failures in last 24h",
                    }
                )

            severity = "ok"
            if any(w["code"] == "profile_inactive" for w in warnings):
                severity = "critical"
            elif warnings:
                severity = "warning"

            items.append(
                {
                    "platform": platform,
                    "active": active,
                    "status": status,
                    "last_used_at": last_used_at,
                    "severity": severity,
                    "warnings": warnings,
                }
            )

        return {
            "generated_at": _now_iso(),
            "checkpoint_failures_24h": int(failed_checkpoints),
            "platforms": items,
        }

    def create_alert(
        self,
        *,
        severity: str,
        alert_type: str,
        dedupe_key: str,
        message: str,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                """
                SELECT id, occurrence_count
                FROM cockpit_alerts
                WHERE dedupe_key = ? AND status != 'resolved'
                ORDER BY id DESC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE cockpit_alerts
                    SET severity = ?,
                        alert_type = ?,
                        message = ?,
                        meta_json = ?,
                        occurrence_count = ?,
                        last_seen_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (severity, alert_type, message, json.dumps(meta or {}), int(existing[1]) + 1, now, now, int(existing[0])),
                )
                alert_id = int(existing[0])
            else:
                conn.execute(
                    """
                    INSERT INTO cockpit_alerts
                    (severity, alert_type, dedupe_key, message, status, meta_json, occurrence_count, first_seen_at, last_seen_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'new', ?, 1, ?, ?, ?, ?)
                    """,
                    (severity, alert_type, dedupe_key, message, json.dumps(meta or {}), now, now, now, now),
                )
                alert_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.commit()
        return self.get_alert(alert_id)

    def list_alerts(self, status: str | None = None, severity: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        query = [
            """
            SELECT id, severity, alert_type, dedupe_key, message, status, meta_json, occurrence_count,
                   first_seen_at, last_seen_at, acked_at, resolved_at, created_at, updated_at
            FROM cockpit_alerts
            WHERE 1=1
            """
        ]
        params: list[object] = []
        if status:
            query.append("AND status = ?")
            params.append(status)
        if severity:
            query.append("AND severity = ?")
            params.append(severity)
        query.append("ORDER BY id DESC LIMIT ?")
        params.append(max(1, min(limit, 500)))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("\n".join(query), params).fetchall()

        items: list[dict[str, object]] = []
        for row in rows:
            try:
                meta = json.loads(str(row[6] or "{}"))
            except json.JSONDecodeError:
                meta = {"raw": row[6]}
            items.append(
                {
                    "id": int(row[0]),
                    "severity": str(row[1]),
                    "alert_type": str(row[2]),
                    "dedupe_key": str(row[3]),
                    "message": str(row[4]),
                    "status": str(row[5]),
                    "meta": meta,
                    "occurrence_count": int(row[7]),
                    "first_seen_at": row[8],
                    "last_seen_at": row[9],
                    "acked_at": row[10],
                    "resolved_at": row[11],
                    "created_at": row[12],
                    "updated_at": row[13],
                }
            )
        return items

    def get_alert(self, alert_id: int) -> dict[str, object]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, severity, alert_type, dedupe_key, message, status, meta_json, occurrence_count,
                       first_seen_at, last_seen_at, acked_at, resolved_at, created_at, updated_at
                FROM cockpit_alerts
                WHERE id = ?
                """,
                (alert_id,),
            ).fetchone()
        if row is None:
            raise ValueError("alert not found")
        try:
            meta = json.loads(str(row[6] or "{}"))
        except json.JSONDecodeError:
            meta = {"raw": row[6]}
        return {
            "id": int(row[0]),
            "severity": str(row[1]),
            "alert_type": str(row[2]),
            "dedupe_key": str(row[3]),
            "message": str(row[4]),
            "status": str(row[5]),
            "meta": meta,
            "occurrence_count": int(row[7]),
            "first_seen_at": row[8],
            "last_seen_at": row[9],
            "acked_at": row[10],
            "resolved_at": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }

    def update_alert_status(self, alert_id: int, status: str) -> dict[str, object]:
        if status not in {"acked", "resolved"}:
            raise ValueError("unsupported status")
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            current = conn.execute("SELECT id FROM cockpit_alerts WHERE id = ?", (alert_id,)).fetchone()
            if not current:
                raise ValueError("alert not found")
            conn.execute(
                """
                UPDATE cockpit_alerts
                SET status = ?,
                    acked_at = CASE WHEN ? = 'acked' THEN ? ELSE acked_at END,
                    resolved_at = CASE WHEN ? = 'resolved' THEN ? ELSE resolved_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, status, now, status, now, now, alert_id),
            )
            conn.commit()
        return self.get_alert(alert_id)

    def get_remediation_policy(self, alert_type: str, platform_scope: str) -> dict[str, object]:
        if platform_scope != REMEDIATION_PLATFORM_GLOBAL and platform_scope not in PLATFORMS:
            raise ValueError(f"unsupported platform_scope: {platform_scope}")
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT alert_type, platform_scope, enabled, cooldown_minutes, max_actions_per_day, updated_by, updated_at
                FROM cockpit_remediation_policies
                WHERE alert_type = ? AND platform_scope = ?
                """,
                (alert_type, platform_scope),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO cockpit_remediation_policies
                    (alert_type, platform_scope, enabled, cooldown_minutes, max_actions_per_day, updated_by, updated_at)
                    VALUES (?, ?, 0, 60, 3, ?, ?)
                    """,
                    (alert_type, platform_scope, "system", now),
                )
                conn.commit()
                row = (alert_type, platform_scope, 0, 60, 3, "system", now)
        return {
            "alert_type": str(row[0]),
            "platform_scope": str(row[1]),
            "enabled": bool(row[2]),
            "cooldown_minutes": int(row[3]),
            "max_actions_per_day": int(row[4]),
            "updated_by": row[5],
            "updated_at": str(row[6]),
        }

    def upsert_remediation_policy(
        self,
        *,
        alert_type: str,
        platform_scope: str,
        enabled: bool | None = None,
        cooldown_minutes: int | None = None,
        max_actions_per_day: int | None = None,
        updated_by: str | None = None,
    ) -> dict[str, object]:
        current = self.get_remediation_policy(alert_type=alert_type, platform_scope=platform_scope)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_remediation_policies
                (alert_type, platform_scope, enabled, cooldown_minutes, max_actions_per_day, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_type, platform_scope) DO UPDATE SET
                  enabled = excluded.enabled,
                  cooldown_minutes = excluded.cooldown_minutes,
                  max_actions_per_day = excluded.max_actions_per_day,
                  updated_by = excluded.updated_by,
                  updated_at = excluded.updated_at
                """,
                (
                    alert_type,
                    platform_scope,
                    int(enabled if enabled is not None else current["enabled"]),
                    int(cooldown_minutes if cooldown_minutes is not None else current["cooldown_minutes"]),
                    int(max_actions_per_day if max_actions_per_day is not None else current["max_actions_per_day"]),
                    updated_by,
                    _now_iso(),
                ),
            )
            conn.commit()
        return self.get_remediation_policy(alert_type=alert_type, platform_scope=platform_scope)

    def list_remediation_policies(self, alert_type: str | None = None, platform_scope: str | None = None) -> list[dict[str, object]]:
        query = [
            """
            SELECT alert_type, platform_scope, enabled, cooldown_minutes, max_actions_per_day, updated_by, updated_at
            FROM cockpit_remediation_policies
            WHERE 1=1
            """
        ]
        params: list[object] = []
        if alert_type:
            query.append("AND alert_type = ?")
            params.append(alert_type)
        if platform_scope:
            query.append("AND platform_scope = ?")
            params.append(platform_scope)
        query.append("ORDER BY updated_at DESC")
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("\n".join(query), params).fetchall()
        return [
            {
                "alert_type": str(row[0]),
                "platform_scope": str(row[1]),
                "enabled": bool(row[2]),
                "cooldown_minutes": int(row[3]),
                "max_actions_per_day": int(row[4]),
                "updated_by": row[5],
                "updated_at": str(row[6]),
            }
            for row in rows
        ]

    def create_remediation_run(
        self,
        *,
        alert_id: int | None,
        alert_type: str,
        platform_scope: str,
        trigger_mode: str,
        dry_run: bool,
        status: str,
        notes: str,
        actions: list[dict[str, object]],
    ) -> dict[str, object]:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_remediation_runs
                (alert_id, alert_type, platform_scope, trigger_mode, dry_run, status, notes, actions_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (alert_id, alert_type, platform_scope, trigger_mode, int(dry_run), status, notes, json.dumps(actions), _now_iso()),
            )
            run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.commit()
        return self.get_remediation_run(run_id)

    def get_remediation_run(self, run_id: int) -> dict[str, object]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, alert_id, alert_type, platform_scope, trigger_mode, dry_run, status, notes, actions_json, created_at
                FROM cockpit_remediation_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError("remediation run not found")
        return {
            "id": int(row[0]),
            "alert_id": row[1],
            "alert_type": str(row[2]),
            "platform_scope": str(row[3]),
            "trigger_mode": str(row[4]),
            "dry_run": bool(row[5]),
            "status": str(row[6]),
            "notes": str(row[7] or ""),
            "actions": json.loads(str(row[8] or "[]")),
            "created_at": str(row[9]),
        }

    def list_remediation_runs(self, limit: int = 100, alert_type: str | None = None, platform_scope: str | None = None) -> list[dict[str, object]]:
        query = [
            """
            SELECT id, alert_id, alert_type, platform_scope, trigger_mode, dry_run, status, notes, actions_json, created_at
            FROM cockpit_remediation_runs
            WHERE 1=1
            """
        ]
        params: list[object] = []
        if alert_type:
            query.append("AND alert_type = ?")
            params.append(alert_type)
        if platform_scope:
            query.append("AND platform_scope = ?")
            params.append(platform_scope)
        query.append("ORDER BY id DESC LIMIT ?")
        params.append(max(1, min(limit, 500)))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("\n".join(query), params).fetchall()
        return [
            {
                "id": int(row[0]),
                "alert_id": row[1],
                "alert_type": str(row[2]),
                "platform_scope": str(row[3]),
                "trigger_mode": str(row[4]),
                "dry_run": bool(row[5]),
                "status": str(row[6]),
                "notes": str(row[7] or ""),
                "actions": json.loads(str(row[8] or "[]")),
                "created_at": str(row[9]),
            }
            for row in rows
        ]

    def remediation_gate(self, *, alert_type: str, platform_scope: str) -> dict[str, object]:
        policy = self.get_remediation_policy(alert_type=alert_type, platform_scope=platform_scope)
        with sqlite3.connect(self.db_path) as conn:
            today_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM cockpit_remediation_runs
                    WHERE alert_type = ? AND platform_scope = ?
                      AND dry_run = 0
                      AND status = 'executed'
                      AND date(created_at) = date('now')
                    """,
                    (alert_type, platform_scope),
                ).fetchone()[0]
            )
            last_executed = conn.execute(
                """
                SELECT created_at FROM cockpit_remediation_runs
                WHERE alert_type = ? AND platform_scope = ?
                  AND dry_run = 0 AND status = 'executed'
                ORDER BY created_at DESC LIMIT 1
                """,
                (alert_type, platform_scope),
            ).fetchone()

        reasons: list[str] = []
        if not policy["enabled"]:
            reasons.append("disabled")
        if today_count >= policy["max_actions_per_day"]:
            reasons.append("max_actions_per_day_reached")
        if last_executed:
            elapsed_minutes = (datetime.now(UTC) - datetime.fromisoformat(str(last_executed[0]))).total_seconds() / 60
            if elapsed_minutes < float(policy["cooldown_minutes"]):
                reasons.append("cooldown_active")
        return {
            "allowed": not reasons,
            "reasons": reasons,
            "policy": policy,
            "today_count": today_count,
            "last_executed_at": last_executed[0] if last_executed else None,
        }

    def ops_command_center_summary(self) -> dict[str, object]:
        return {
            "queue_counts": self.queue_snapshot(),
            "active_platform_states": self.list_session_workflows(session_key="default"),
            "critical_alerts_count": len(self.list_alerts(status="new", severity="critical", limit=500)),
            "latest_operator_actions": self.list_operator_events(limit=8),
            "quick_actions": [
                {"id": "approve_checkpoint", "label": "Approve Checkpoint", "endpoint": "/api/policy/checkpoint"},
                {"id": "pause_linkedin", "label": "Pause LinkedIn", "endpoint": "/api/scheduler/linkedin"},
                {"id": "refresh_screenshot", "label": "Request Screenshot", "endpoint": "/api/live/screenshot/capture"},
            ],
        }

    def latest_screenshot(self, screenshot_dir: Path) -> dict[str, object]:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        candidates = [
            p for p in screenshot_dir.glob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        if not candidates:
            return {
                "available": False,
                "filename": None,
                "captured_at": None,
                "size_bytes": 0,
                "url": None,
            }

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        return {
            "available": True,
            "filename": latest.name,
            "captured_at": datetime.fromtimestamp(latest.stat().st_mtime, UTC).isoformat(),
            "size_bytes": latest.stat().st_size,
            "url": f"/api/live/screenshot/{latest.name}",
        }

    def get_policy_settings(self, policy_key: str = POLICY_KEY_DEFAULT) -> dict[str, object]:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT policy_key, daily_apply_cap, min_seconds_between_actions, require_human_checkpoint, updated_at
                FROM cockpit_policy_settings
                WHERE policy_key = ?
                """,
                (policy_key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO cockpit_policy_settings
                    (policy_key, daily_apply_cap, min_seconds_between_actions, require_human_checkpoint, updated_at)
                    VALUES (?, 20, 20, 1, ?)
                    """,
                    (policy_key, now),
                )
                conn.commit()
                row = (policy_key, 20, 20, 1, now)

        return {
            "policy_key": str(row[0]),
            "daily_apply_cap": int(row[1]),
            "min_seconds_between_actions": int(row[2]),
            "require_human_checkpoint": bool(row[3]),
            "updated_at": str(row[4]),
        }

    def update_policy_settings(
        self,
        policy_key: str = POLICY_KEY_DEFAULT,
        daily_apply_cap: int | None = None,
        min_seconds_between_actions: int | None = None,
        require_human_checkpoint: bool | None = None,
    ) -> dict[str, object]:
        current = self.get_policy_settings(policy_key=policy_key)
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_policy_settings
                (policy_key, daily_apply_cap, min_seconds_between_actions, require_human_checkpoint, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(policy_key) DO UPDATE SET
                  daily_apply_cap = excluded.daily_apply_cap,
                  min_seconds_between_actions = excluded.min_seconds_between_actions,
                  require_human_checkpoint = excluded.require_human_checkpoint,
                  updated_at = excluded.updated_at
                """,
                (
                    policy_key,
                    int(daily_apply_cap if daily_apply_cap is not None else current["daily_apply_cap"]),
                    int(
                        min_seconds_between_actions
                        if min_seconds_between_actions is not None
                        else current["min_seconds_between_actions"]
                    ),
                    int(
                        require_human_checkpoint
                        if require_human_checkpoint is not None
                        else current["require_human_checkpoint"]
                    ),
                    now,
                ),
            )
            conn.commit()
        return self.get_policy_settings(policy_key=policy_key)

    def record_policy_event(self, event_type: str, meta: dict[str, object] | None = None, policy_key: str = POLICY_KEY_DEFAULT) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cockpit_policy_events (policy_key, event_type, created_at, meta_json)
                VALUES (?, ?, ?, ?)
                """,
                (policy_key, event_type, _now_iso(), json.dumps(meta or {})),
            )
            conn.commit()

    def policy_status(self, policy_key: str = POLICY_KEY_DEFAULT) -> dict[str, object]:
        settings = self.get_policy_settings(policy_key=policy_key)
        with sqlite3.connect(self.db_path) as conn:
            today_count = conn.execute(
                """
                SELECT COUNT(*) FROM cockpit_policy_events
                WHERE policy_key = ?
                  AND event_type = 'apply'
                  AND date(created_at) = date('now')
                """,
                (policy_key,),
            ).fetchone()[0]
            last_action = conn.execute(
                """
                SELECT created_at FROM cockpit_policy_events
                WHERE policy_key = ? AND event_type IN ('action', 'apply', 'submit')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (policy_key,),
            ).fetchone()
            last_checkpoint = conn.execute(
                """
                SELECT created_at FROM cockpit_policy_events
                WHERE policy_key = ? AND event_type = 'checkpoint_approved'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (policy_key,),
            ).fetchone()

        seconds_since_last_action: float | None = None
        if last_action:
            delta = datetime.now(UTC) - datetime.fromisoformat(str(last_action[0]))
            seconds_since_last_action = delta.total_seconds()

        checkpoint_valid = True
        if settings["require_human_checkpoint"]:
            checkpoint_valid = bool(last_checkpoint)

        return {
            "policy_key": policy_key,
            "today_apply_count": int(today_count),
            "remaining_apply_quota": max(0, settings["daily_apply_cap"] - int(today_count)),
            "seconds_since_last_action": seconds_since_last_action,
            "checkpoint_valid": checkpoint_valid,
            "last_checkpoint_at": last_checkpoint[0] if last_checkpoint else None,
            "settings": settings,
        }
