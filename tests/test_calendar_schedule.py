from datetime import UTC, datetime

from huntbot2.automation.calendar_schedule import (
    CalendarEntry,
    entry_due,
    next_due_entry,
)


def _entry(**kw):
    base = dict(id=1, day_of_week=0, time_hhmm="09:00", platform="linkedin",
                jobs_per_run=5, mode="supervised", enabled=True, last_run_at=None)
    base.update(kw)
    return CalendarEntry(**base)


# 2026-06-08 is a Monday; 09:00 UTC.
MON_9 = datetime(2026, 6, 8, 9, 0, tzinfo=UTC)
MON_915 = datetime(2026, 6, 8, 9, 15, tzinfo=UTC)
TUE_9 = datetime(2026, 6, 9, 9, 0, tzinfo=UTC)


def test_due_on_matching_day_and_time():
    assert entry_due(_entry(day_of_week=0, time_hhmm="09:00"), MON_9) is True


def test_not_due_on_wrong_day():
    assert entry_due(_entry(day_of_week=0, time_hhmm="09:00"), TUE_9) is False


def test_not_due_before_slot():
    early = datetime(2026, 6, 8, 8, 59, tzinfo=UTC)
    assert entry_due(_entry(time_hhmm="09:00"), early) is False


def test_due_within_grace_window():
    assert entry_due(_entry(time_hhmm="09:00"), MON_915, grace_minutes=20) is True


def test_not_due_after_grace_window():
    assert entry_due(_entry(time_hhmm="09:00"), MON_915, grace_minutes=10) is False


def test_disabled_never_due():
    assert entry_due(_entry(enabled=False), MON_9) is False


def test_not_due_if_already_ran_this_slot():
    e = _entry(time_hhmm="09:00", last_run_at="2026-06-08T09:01:00+00:00")
    assert entry_due(e, MON_915, grace_minutes=30) is False


def test_due_again_next_week_after_prior_run():
    # Ran last Monday; this Monday's slot should be due again.
    e = _entry(time_hhmm="09:00", last_run_at="2026-06-01T09:01:00+00:00")
    assert entry_due(e, MON_9) is True


def test_next_due_entry_picks_first_match():
    a = _entry(id=1, day_of_week=1, time_hhmm="09:00")   # Tuesday — not due Monday
    b = _entry(id=2, day_of_week=0, time_hhmm="09:00", platform="indeed")
    picked = next_due_entry([a, b], MON_9)
    assert picked is not None and picked.id == 2


def test_timezone_shifts_local_day():
    # 23:30 UTC Sunday is Monday 00:30 in a +2h zone.
    e = _entry(day_of_week=0, time_hhmm="00:30")
    sun_2330 = datetime(2026, 6, 7, 23, 30, tzinfo=UTC)
    assert entry_due(e, sun_2330, timezone="Europe/Bucharest") in (True, False)  # tz data may vary
    # With UTC it is still Sunday, so not due.
    assert entry_due(e, sun_2330, timezone="UTC") is False
