"""US equity (XNYS) session calendar shared by US providers and daemons."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

NY = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def calendar():
    return xcals.get_calendar("XNYS", start="1970-01-01", end=f"{datetime.now(UTC).year + 2}-12-31")


def is_session(day: date) -> bool:
    return bool(calendar().is_session(day.isoformat()))


def bounds(day: date):
    cal = calendar()
    if not cal.is_session(day.isoformat()):
        return None
    return (cal.session_open(day.isoformat()).to_pydatetime(),
            cal.session_close(day.isoformat()).to_pydatetime())


def latest_completed(now: datetime, *, grace_minutes: int = 30) -> date:
    cal = calendar()
    day = now.astimezone(NY).date()
    for session in reversed(cal.sessions_in_range(day - timedelta(days=20), day)):
        if cal.session_close(session).to_pydatetime() + timedelta(minutes=grace_minutes) <= now:
            return session.date()
    raise RuntimeError("No completed US session found")


def next_scheduled_run(now: datetime, at: Sequence[time], *, horizon_days: int = 14) -> datetime:
    """Return the first New York wall-clock slot in ``at`` after ``now`` on an XNYS session.

    Slots are interpreted in America/New_York, so the UTC result follows DST.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not at:
        raise ValueError("at least one scheduled time is required")
    local_day = now.astimezone(NY).date()
    for offset in range(horizon_days + 1):
        day = local_day + timedelta(days=offset)
        if not is_session(day):
            continue
        for slot in sorted(at):
            candidate = datetime.combine(day, slot, tzinfo=NY).astimezone(UTC)
            if candidate > now:
                return candidate
    raise RuntimeError(f"No XNYS session within {horizon_days} days of {now.isoformat()}")


__all__ = ["NY", "bounds", "calendar", "is_session", "latest_completed", "next_scheduled_run"]
