from datetime import UTC, date, datetime

import pytest

from factorlab.calendars.globex import (
    is_open,
    next_open,
    session_close,
    session_date,
    trade_date,
)
from factorlab.calendars.us import NY


def ny(*args: int) -> datetime:
    return datetime(*args, tzinfo=NY)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (ny(2026, 10, 18, 17, 59), None),  # Sunday before the open
        (ny(2026, 10, 18, 18, 0), date(2026, 10, 19)),  # Sunday open trades Monday
        (ny(2026, 10, 19, 9, 30), date(2026, 10, 19)),
        (ny(2026, 10, 19, 16, 59), date(2026, 10, 19)),
        (ny(2026, 10, 19, 17, 0), None),  # daily break
        (ny(2026, 10, 19, 17, 59), None),
        (ny(2026, 10, 19, 18, 0), date(2026, 10, 20)),
        (ny(2026, 10, 22, 23, 0), date(2026, 10, 23)),  # Thursday night trades Friday
        (ny(2026, 10, 23, 16, 59), date(2026, 10, 23)),
        (ny(2026, 10, 23, 17, 0), None),  # weekend close
        (ny(2026, 10, 23, 19, 0), None),
        (ny(2026, 10, 24, 12, 0), None),  # Saturday
    ],
)
def test_trade_date(at: datetime, expected: date | None) -> None:
    assert trade_date(at) == expected
    assert is_open(at) is (expected is not None)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (ny(2026, 10, 19, 10, 0), date(2026, 10, 19)),  # open: its own trade date
        (ny(2026, 10, 19, 17, 30), date(2026, 10, 19)),  # break: the session that closed
        (ny(2026, 10, 23, 20, 0), date(2026, 10, 23)),  # Friday night
        (ny(2026, 10, 24, 12, 0), date(2026, 10, 23)),  # Saturday
        (ny(2026, 10, 25, 12, 0), date(2026, 10, 23)),  # Sunday before the open
    ],
)
def test_session_date(at: datetime, expected: date) -> None:
    assert session_date(at) == expected


def test_trade_date_follows_dst() -> None:
    # 2026-11-01 ends DST: 23:00 UTC is 18:00 EST on Sunday, the open.
    assert trade_date(datetime(2026, 11, 1, 22, 59, tzinfo=UTC)) is None
    assert trade_date(datetime(2026, 11, 1, 23, 0, tzinfo=UTC)) == date(2026, 11, 2)


def test_session_close_and_next_open() -> None:
    assert session_close(date(2026, 10, 23)) == datetime(2026, 10, 23, 21, 0, tzinfo=UTC)
    assert next_open(ny(2026, 10, 19, 17, 30)) == ny(2026, 10, 19, 18, 0).astimezone(UTC)
    assert next_open(ny(2026, 10, 23, 17, 0)) == ny(2026, 10, 25, 18, 0).astimezone(UTC)
    assert next_open(ny(2026, 10, 19, 10, 0)) == ny(2026, 10, 19, 18, 0).astimezone(UTC)


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        trade_date(datetime(2026, 10, 19, 12, 0))
