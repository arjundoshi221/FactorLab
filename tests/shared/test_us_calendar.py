"""XNYS schedule helper: New York wall-clock slots on sessions, DST-aware."""

from datetime import UTC, date, datetime, time

import pytest

from factorlab.shared.runtime.us_calendar import is_session, next_scheduled_run

SLOTS = (time(6, 0), time(16, 30))


def test_next_slot_same_day():
    # Thu 2026-09-24 12:00 UTC = 08:00 EDT -> 16:30 EDT = 20:30 UTC
    assert next_scheduled_run(datetime(2026, 9, 24, 12, 0, tzinfo=UTC), SLOTS) == datetime(
        2026, 9, 24, 20, 30, tzinfo=UTC)


def test_weekend_rolls_to_monday_open_slot():
    # Fri 2026-09-25 21:00 UTC is after the close slot -> Mon 06:00 EDT
    assert next_scheduled_run(datetime(2026, 9, 25, 21, 0, tzinfo=UTC), SLOTS) == datetime(
        2026, 9, 28, 10, 0, tzinfo=UTC)


def test_slots_follow_dst():
    # 2026-11-02 (Mon) is after the DST change: 06:00 EST = 11:00 UTC
    assert next_scheduled_run(datetime(2026, 10, 30, 22, 0, tzinfo=UTC), SLOTS) == datetime(
        2026, 11, 2, 11, 0, tzinfo=UTC)


def test_exchange_holiday_is_skipped():
    assert not is_session(date(2026, 11, 26))  # Thanksgiving
    assert next_scheduled_run(datetime(2026, 11, 25, 22, 0, tzinfo=UTC), SLOTS) == datetime(
        2026, 11, 27, 11, 0, tzinfo=UTC)


def test_rejects_naive_and_empty_schedules():
    with pytest.raises(ValueError):
        next_scheduled_run(datetime(2026, 9, 24, 12, 0), SLOTS)  # noqa: DTZ001 - naive on purpose
    with pytest.raises(ValueError):
        next_scheduled_run(datetime(2026, 9, 24, 12, 0, tzinfo=UTC), ())
