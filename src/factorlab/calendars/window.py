"""MarketWindow — trading-hours helper backed by ``exchange_calendars``.

Encapsulates the pattern repeated across the live pollers:
  - check whether today is a session on the given calendar (XNYS, XBOM, ...)
  - compute the pre-open / open / close wall-clock instants in the market's tz
  - support a configurable "start polling N minutes before open" buffer

Usage::

    us = MarketWindow(
        calendar_key="XNYS",
        open_time=time(9, 30),
        close_time=time(16, 0),
        tz=ZoneInfo("America/New_York"),
        pre_open_min=30,
    )

    if not us.is_trading_day():
        return ExitCode.NOT_TRADING_DAY

    now = us.now_local()
    if now < us.poll_start():
        sleep_until(us.poll_start())
    elif now >= us.close_dt():
        run_final_sweep()

The calendar is lazily resolved (``exchange_calendars`` is expensive to import
at module load).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    import exchange_calendars as xcals  # noqa: F401


class MarketWindow:
    """Per-market trading-hours façade. All wall-clock returns are tz-aware."""

    def __init__(
        self,
        calendar_key: str,
        *,
        open_time: time,
        close_time: time,
        tz: ZoneInfo,
        pre_open_min: int = 0,
    ) -> None:
        self.calendar_key = calendar_key
        self.open_time = open_time
        self.close_time = close_time
        self.tz = tz
        self.pre_open_min = pre_open_min
        self._cal = None  # lazy

    @property
    def calendar(self):
        """Return the lazily-resolved ``exchange_calendars`` instance."""
        if self._cal is None:
            import exchange_calendars as xcals
            self._cal = xcals.get_calendar(self.calendar_key)
        return self._cal

    def is_trading_day(self, when: date | None = None) -> bool:
        """True iff *when* (default today in market tz) is a session."""
        when = when or self.now_local().date()
        iso = when.isoformat()
        return len(self.calendar.sessions_in_range(iso, iso)) > 0

    def now_local(self) -> datetime:
        """Current time in the market's local tz."""
        return datetime.now(self.tz)

    def open_dt(self, day: date | None = None) -> datetime:
        day = day or self.now_local().date()
        return datetime.combine(day, self.open_time, tzinfo=self.tz)

    def close_dt(self, day: date | None = None) -> datetime:
        day = day or self.now_local().date()
        return datetime.combine(day, self.close_time, tzinfo=self.tz)

    def poll_start(self, day: date | None = None) -> datetime:
        """The wall-clock instant polling should begin (open minus pre-open buffer)."""
        return self.open_dt(day) - timedelta(minutes=self.pre_open_min)

    def next_open(self, after: datetime | None = None) -> datetime:
        """Return the next session's open in market tz, strictly after *after*
        (default = now). Walks forward at most 30 days.
        """
        after = after or self.now_local()
        for offset in range(0, 30):
            day = (after + timedelta(days=offset)).date()
            if self.is_trading_day(day):
                open_dt = self.open_dt(day)
                if open_dt > after:
                    return open_dt
        raise RuntimeError(
            f"no trading session within 30 days on {self.calendar_key}"
        )


__all__ = ["MarketWindow"]
