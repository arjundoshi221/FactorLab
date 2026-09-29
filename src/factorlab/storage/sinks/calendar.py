"""Market calendars the DB service uses to derive ``session``, ``trade_date`` and period keys.

Session tagging is market knowledge, not provider knowledge (07 §5.3): the
same NSE minute gets the same session whichever vendor delivered it. Hours
match what ``V2IndiaStorage`` and ``V2USStorage`` tag today.

Period bars (daily/weekly/monthly) are keyed at exchange-local midnight of the
trade date, converted to UTC. That is what production has always written for
US daily bars (``V2USStorage.write_daily``), so every provider lands on the
same key. Doc 06 §13.2 says 00:00 UTC; production wins to avoid re-keying
(07 §15.1). A provider may send any instant whose exchange-local date is the
trade date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

_MARKETS: dict[str, tuple[ZoneInfo, time, time]] = {
    "IN": (ZoneInfo("Asia/Kolkata"), time(9, 15), time(15, 30)),
    "US": (ZoneInfo("America/New_York"), time(9, 30), time(16, 0)),
}
PERIOD_RESOLUTIONS = frozenset({"daily", "weekly", "monthly"})


def _market(country_code: str) -> tuple[ZoneInfo, time, time]:
    try:
        return _MARKETS[country_code]
    except KeyError:
        raise ValueError(f"no market calendar for country {country_code!r}") from None


def session_for(country_code: str, bar_time: datetime, resolution: str) -> str:
    if resolution in PERIOD_RESOLUTIONS:
        return "regular"
    zone, opens, closes = _market(country_code)
    local = bar_time.astimezone(zone).time()
    if local < opens:
        return "pre"
    return "regular" if local < closes else "post"


def trade_date_for(country_code: str, bar_time: datetime, resolution: str) -> date:
    """Session-local calendar date of the bar."""
    zone, _, _ = _market(country_code)
    return bar_time.astimezone(zone).date()


def canonical_bar_time(country_code: str, bar_time: datetime, resolution: str) -> datetime:
    """Intraday bars keep their open time; period bars move to local midnight (UTC)."""
    if resolution not in PERIOD_RESOLUTIONS:
        return bar_time.astimezone(UTC)
    zone, _, _ = _market(country_code)
    day = trade_date_for(country_code, bar_time, resolution)
    return datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)


__all__ = ["PERIOD_RESOLUTIONS", "canonical_bar_time", "session_for", "trade_date_for"]
