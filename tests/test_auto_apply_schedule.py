from datetime import UTC, datetime, timedelta

from huntbot2.automation.auto_apply import (
    ScheduleConfig,
    next_run_at,
    plan_apply_count,
    should_run_now,
    within_window,
)


def _utc(h, m=0):
    return datetime(2026, 6, 11, h, m, tzinfo=UTC)


# ── window ────────────────────────────────────────────────────────────────────

def test_within_window_daytime():
    assert within_window(_utc(9), "08:00", "20:00") is True
    assert within_window(_utc(7), "08:00", "20:00") is False
    assert within_window(_utc(21), "08:00", "20:00") is False


def test_within_window_overnight():
    assert within_window(_utc(23), "20:00", "06:00") is True
    assert within_window(_utc(3), "20:00", "06:00") is True
    assert within_window(_utc(12), "20:00", "06:00") is False


# ── should_run_now ────────────────────────────────────────────────────────────

def _cfg(**kw):
    base = dict(enabled=True, interval_minutes=180, jobs_per_run=5,
                window_start="08:00", window_end="20:00", timezone="UTC", daily_cap=20)
    base.update(kw)
    return ScheduleConfig(**base)


def test_disabled_never_runs():
    assert should_run_now(_utc(10), None, _cfg(enabled=False)) is False


def test_outside_window_does_not_run():
    assert should_run_now(_utc(6), None, _cfg()) is False


def test_first_run_inside_window_runs():
    assert should_run_now(_utc(10), None, _cfg()) is True


def test_interval_not_elapsed_blocks():
    last = _utc(10)
    assert should_run_now(_utc(11), last, _cfg(interval_minutes=180)) is False


def test_interval_elapsed_runs():
    last = _utc(10)
    assert should_run_now(_utc(13, 1), last, _cfg(interval_minutes=180)) is True


# ── next_run_at ───────────────────────────────────────────────────────────────

def test_next_run_none_when_disabled():
    assert next_run_at(_utc(10), None, _cfg(enabled=False)) is None


def test_next_run_is_last_plus_interval():
    last = _utc(10)
    assert next_run_at(_utc(11), last, _cfg(interval_minutes=180)) == last + timedelta(minutes=180)


# ── plan_apply_count ──────────────────────────────────────────────────────────

def test_plan_respects_per_run_limit():
    assert plan_apply_count(jobs_per_run=5, daily_cap=20, applied_today=0, available_tailored=50) == 5


def test_plan_clamped_by_daily_remaining():
    assert plan_apply_count(jobs_per_run=5, daily_cap=20, applied_today=18, available_tailored=50) == 2


def test_plan_clamped_by_available_jobs():
    assert plan_apply_count(jobs_per_run=5, daily_cap=20, applied_today=0, available_tailored=1) == 1


def test_plan_zero_when_cap_reached():
    assert plan_apply_count(jobs_per_run=5, daily_cap=20, applied_today=20, available_tailored=50) == 0


def test_plan_never_negative():
    assert plan_apply_count(jobs_per_run=5, daily_cap=20, applied_today=99, available_tailored=50) == 0
