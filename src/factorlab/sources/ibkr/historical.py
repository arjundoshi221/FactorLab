"""Rate-limited historical bar fetcher.

Thin wrapper over ``ib.reqHistoricalData`` that goes through ``RateLimiter``
and retries with exponential backoff on IBKR error 162 (pacing violation).

Bulk daily backfill should still prefer EODHD; IBKR historical is for
deep-history and validation samples per docs/data-sources/us/ibkr.md §6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from factorlab.sources.ibkr.errors import IBKRPacingError
from factorlab.sources.ibkr.pacing import RateLimiter, backoff_for

if TYPE_CHECKING:
    from ib_async import IB, BarData, Contract

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    """Normalized daily bar. ``date`` is a UTC-aware ``datetime`` at midnight."""

    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    wap: float
    bar_count: int


def _normalize_date(raw: object) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    # ib_async can hand back a `date` object for daily bars
    try:
        # date has isoformat()
        parsed = datetime.fromisoformat(str(raw))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"Cannot parse IBKR bar date {raw!r}") from exc


def _to_bar(b: BarData) -> HistoricalBar:
    return HistoricalBar(
        date=_normalize_date(b.date),
        open=float(b.open),
        high=float(b.high),
        low=float(b.low),
        close=float(b.close),
        volume=float(b.volume),
        wap=float(b.average),
        bar_count=int(b.barCount),
    )


def fetch_daily_bars(
    ib: IB,
    contract: Contract,
    *,
    duration: str = "1 Y",
    what_to_show: str = "TRADES",
    use_rth: bool = True,
    end_datetime: str = "",
    limiter: RateLimiter | None = None,
    max_retries: int = 4,
) -> list[HistoricalBar]:
    """Fetch daily bars with pacing + error-162 backoff.

    ``BID_ASK`` counts as weight 2 per IBKR docs; we honor that. Other
    ``whatToShow`` values are weight 1.
    """
    limiter = limiter if limiter is not None else RateLimiter()
    weight = 2 if what_to_show.upper() == "BID_ASK" else 1
    key = f"{contract.conId}:{contract.exchange}:1d:{what_to_show}"

    attempt = 0
    while True:
        attempt += 1
        limiter.wait_for_slot(key, weight=weight)
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_datetime,
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
            )
        except Exception as exc:
            msg = str(exc)
            if "162" in msg and attempt <= max_retries:
                delay = backoff_for(attempt)
                log.warning(
                    "IBKR error 162 (pacing) on %s attempt=%d; sleeping %.1fs",
                    contract.symbol, attempt, delay,
                )
                limiter._sleep(delay)  # reuse limiter's clock/sleep for tests
                continue
            if "162" in msg:
                raise IBKRPacingError(
                    f"IBKR pacing violation exhausted retries for {contract.symbol}: {msg}"
                ) from exc
            raise

        return [_to_bar(b) for b in bars]
