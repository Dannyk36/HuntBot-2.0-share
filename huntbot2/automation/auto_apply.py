"""Scheduled auto-apply engine.

Decides *when* a scheduled run is due and *how many* applications it may submit,
within the operator's safety envelope (time window, interval, daily cap, and the
per-run jobs limit). The decision logic here is pure and deterministic — the
reliability core of the feature — so it is unit-tested without a browser, DB, or
clock dependency. The background loop that calls it lives in the web app.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time as dtime, timedelta


@dataclass
class ScheduleConfig:
    enabled: bool = False
    interval_minutes: int = 180          # minimum gap between scheduled runs
    jobs_per_run: int = 5                # max applications submitted per run
    window_start: str = "08:00"          # local-time active window (inclusive)
    window_end: str = "20:00"
    timezone: str = "UTC"
    daily_cap: int = 20                  # hard ceiling of applications per day


def _parse_hhmm(value: str) -> dtime:
    hh, _, mm = (value or "00:00").partition(":")
    return dtime(int(hh or 0), int(mm or 0))


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return UTC


def within_window(now_local: datetime, window_start: str, window_end: str) -> bool:
    """True if the local time falls inside [start, end]. Supports overnight
    windows where end < start (e.g. 20:00–06:00)."""
    t = now_local.timetz().replace(tzinfo=None)
    start, end = _parse_hhmm(window_start), _parse_hhmm(window_end)
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def should_run_now(
    now_utc: datetime,
    last_run_at: datetime | None,
    config: ScheduleConfig,
) -> bool:
    """Whether a scheduled run is due: enabled, inside the window, and at least
    interval_minutes since the last run."""
    if not config.enabled:
        return False
    now_local = now_utc.astimezone(_zone(config.timezone))
    if not within_window(now_local, config.window_start, config.window_end):
        return False
    if last_run_at is None:
        return True
    return (now_utc - last_run_at) >= timedelta(minutes=max(1, config.interval_minutes))


def next_run_at(
    now_utc: datetime,
    last_run_at: datetime | None,
    config: ScheduleConfig,
) -> datetime | None:
    """Best-effort estimate of the next eligible run time (UTC), or None when
    scheduling is disabled. Used for the dashboard's 'next run' display."""
    if not config.enabled:
        return None
    if should_run_now(now_utc, last_run_at, config):
        return now_utc
    if last_run_at is None:
        return now_utc
    return last_run_at + timedelta(minutes=max(1, config.interval_minutes))


def plan_apply_count(
    *,
    jobs_per_run: int,
    daily_cap: int,
    applied_today: int,
    available_tailored: int,
) -> int:
    """How many applications this run may submit: the per-run limit, clamped by
    the day's remaining quota and the number of tailored jobs actually ready."""
    remaining_today = max(0, daily_cap - max(0, applied_today))
    return max(0, min(max(0, jobs_per_run), remaining_today, max(0, available_tailored)))


def load_schedule_config(daily_cap: int = 20) -> ScheduleConfig:
    """Build a ScheduleConfig from the .env-backed settings the cockpit writes.
    Defaults are safe (disabled) so nothing runs until the operator opts in."""
    from huntbot2.config.env_store import get as env_get

    def _int(key: str, default: int) -> int:
        try:
            return int(env_get(key, str(default)) or default)
        except (TypeError, ValueError):
            return default

    enabled = (env_get("HUNTBOT2_SCHEDULE_ENABLED", "false") or "false").lower() == "true"
    return ScheduleConfig(
        enabled=enabled,
        interval_minutes=max(1, _int("HUNTBOT2_SCHEDULE_INTERVAL_MIN", 180)),
        jobs_per_run=max(0, _int("HUNTBOT2_SCHEDULE_JOBS_PER_RUN", 5)),
        window_start=env_get("HUNTBOT2_SCHEDULE_WINDOW_START", "08:00") or "08:00",
        window_end=env_get("HUNTBOT2_SCHEDULE_WINDOW_END", "20:00") or "20:00",
        timezone=env_get("HUNTBOT2_SCHEDULE_TIMEZONE", "UTC") or "UTC",
        daily_cap=daily_cap,
    )
