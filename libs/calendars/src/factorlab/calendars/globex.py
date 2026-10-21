"""CME Globex weekly session window, used for US futures (F-043).

Globex trades from Sunday 18:00 to Friday 17:00 New York time, with a daily break
from 17:00 to 18:00. A session that opens at 18:00 belongs to the next trade date,
so Sunday evening trades Monday's date. Exchange holidays are not modelled: a
holiday session looks open here and simply returns unchanged quotes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from factorlab.calendars.us import NY

OPEN = time(18, 0)
CLOSE = time(17, 0)
_SATURDAY, _SUNDAY, _FRIDAY = 5, 6, 4


def _local(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    return at.astimezone(NY)


def trade_date(at: datetime) -> date | None:
    """The Globex trade date ``at`` falls in, or ``None`` when Globex is closed."""
    local = _local(at)
    clock, weekday = local.time(), local.weekday()
    if CLOSE <= clock < OPEN or weekday == _SATURDAY:
        return None
    if clock >= OPEN:
        return None if weekday == _FRIDAY else local.date() + timedelta(days=1)
    return None if weekday == _SUNDAY else local.date()


def session_date(at: datetime) -> date:
    """The trade date of the session open at ``at``, else of the session that last closed."""
    open_date = trade_date(at)
    if open_date is not None:
        return open_date
    local = _local(at)
    back = {_SATURDAY: 1, _SUNDAY: 2}.get(local.weekday(), 0)
    return local.date() - timedelta(days=back)


def is_open(at: datetime) -> bool:
    return trade_date(at) is not None


def session_close(day: date) -> datetime:
    """When the session of trade date ``day`` closes (17:00 New York), in UTC."""
    return datetime.combine(day, CLOSE, tzinfo=NY).astimezone(UTC)


def next_open(at: datetime) -> datetime:
    """The next session open strictly after ``at`` (an 18:00 New York boundary), in UTC."""
    local = _local(at)
    day = local.date()
    for offset in range(8):
        candidate = datetime.combine(day + timedelta(days=offset), OPEN, tzinfo=NY)
        if candidate > local and trade_date(candidate) is not None:
            return candidate.astimezone(UTC)
    raise RuntimeError(f"no Globex open within a week of {at.isoformat()}")


__all__ = [
    "CLOSE",
    "OPEN",
    "is_open",
    "next_open",
    "session_close",
    "session_date",
    "trade_date",
]
