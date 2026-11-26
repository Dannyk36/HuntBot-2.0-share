"""Weekly calendar scheduling for auto-apply.

Each entry says: on <day_of_week> at <time>, run discovery+apply for <platform>,
up to <jobs_per_run> applications, in either 'supervised' (stop before submit)
or 'automatic' (submit, still gated by the daily cap + human checkpoint) mode.

The due-calculation is pure and unit-tested; the storage + execution glue lives
in the web app.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time as dtime, timedelta

PLATFORMS = ("linkedin", "indeed", "glassdoor", "handshake", "ziprecruiter", "dice", "wellfound", "monster")
MODES = ("supervised", "automatic")
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class CalendarEntry:
    id: int
    day_of_week: int          # 0=Monday … 6=Sunday
    time_hhmm: str            # "HH:MM" local time
    platform: str
    jobs_per_run: int = 5
    mode: str = "supervised"  # 'supervised' | 'automatic'
    enabled: bool = True
    last_run_at: str | None = None


def _parse_hhmm(value: str) -> dtime:
    hh, _, mm = (value or "00:00").partition(":")
    return dtime(int(hh or 0), int(mm or 0))


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return UTC


def entry_due(
    entry: CalendarEntry,
    now_utc: datetime,
    *,
    timezone: str = "UTC",
    grace_minutes: int = 10,
) -> bool:
    """True if this entry's weekly slot is due now and hasn't already run for it.

    'Due' = it's the right weekday, the local time is within [slot, slot+grace],
    and the entry hasn't run since the slot opened today. The grace window lets a
    once-a-minute poller catch the slot without needing exact-second precision.
    """
    if not entry.enabled:
        return False
    tz = _zone(timezone)
    now_local = now_utc.astimezone(tz)
    if now_local.weekday() != entry.day_of_week:
        return False

    slot_time = _parse_hhmm(entry.time_hhmm)
    slot_local = now_local.replace(
        hour=slot_time.hour, minute=slot_time.minute, second=0, microsecond=0
    )
    if not (slot_local <= now_local <= slot_local + timedelta(minutes=max(1, grace_minutes))):
        return False

    if entry.last_run_at:
        try:
            last = datetime.fromisoformat(entry.last_run_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if last >= slot_local.astimezone(UTC):
                return False  # already ran for this slot
        except ValueError:
            pass
    return True


def next_due_entry(
    entries: list[CalendarEntry],
    now_utc: datetime,
    *,
    timezone: str = "UTC",
    grace_minutes: int = 10,
) -> CalendarEntry | None:
    """Return the first enabled entry that is due now, or None."""
    for entry in entries:
        if entry_due(entry, now_utc, timezone=timezone, grace_minutes=grace_minutes):
            return entry
    return None
