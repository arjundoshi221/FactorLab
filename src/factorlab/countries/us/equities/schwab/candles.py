"""Schwab daily OHLCV bar fetcher.

Schwab returns split-adjusted daily prices and volume. There is no separate
``adj_close`` field -- both ``close`` and ``volume`` are pre-adjusted.
For raw/unadjusted prices use EODHD or IBKR.
"""

import logging
from datetime import datetime, timezone

import pandas as pd

from factorlab.countries.us.equities.schwab.symbols import to_schwab

log = logging.getLogger(__name__)


def fetch_daily_bars(
    client,
    symbol: str,
    *,
    from_date: str | datetime | None = None,
    to_date: str | datetime | None = None,
) -> pd.DataFrame:
    """Fetch daily OHLCV bars for *symbol*.

    Args:
        client: schwab-py client from ``get_client()``
        symbol: Canonical ticker, e.g. ``AAPL``, ``BRK.B`` (translated for the API)
        from_date: Start date (str ``YYYY-MM-DD`` or datetime). Default: ~20yr ago
        to_date: End date. Default: today

    Returns:
        DataFrame with columns:
        ``[bar_time, open, high, low, close, adj_close, volume, source]``
        - ``bar_time``: pandas Timestamp at midnight UTC (matches ``market_us.fact_equity.bar_time``)
        - prices: split-adjusted (Schwab pre-adjusts)
        - ``adj_close``: copied from ``close`` since Schwab doesn't return raw
        - ``source``: ``'schwab'`` (informational; the canonical FK is ``endpoint_id`` set at ingest time)
    """
    schwab_sym = to_schwab(symbol)

    start = _coerce_dt(from_date) if from_date is not None else None
    end = _coerce_dt(to_date) if to_date is not None else None

    r = client.get_price_history_every_day(
        symbol=schwab_sym,
        start_datetime=start,
        end_datetime=end,
        need_extended_hours_data=False,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Schwab daily fetch failed for {symbol}: HTTP {r.status_code} -- {r.text[:200]}"
        )

    payload = r.json()
    if payload.get("empty", True):
        log.info("Schwab returned empty daily bars for %s", symbol)
        return _empty_frame()

    candles = payload.get("candles", [])
    if not candles:
        return _empty_frame()

    df = pd.DataFrame(candles)
    df["bar_time"] = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.normalize()
    df["adj_close"] = df["close"]
    df["source"] = "schwab"
    df = df.drop(columns=["datetime"])
    df = df[["bar_time", "open", "high", "low", "close", "adj_close", "volume", "source"]]
    df = df.drop_duplicates(subset="bar_time").sort_values("bar_time").reset_index(drop=True)
    return df


def _coerce_dt(d) -> datetime:
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    if isinstance(d, str):
        return datetime.fromisoformat(d).replace(tzinfo=timezone.utc) if "T" not in d \
               else datetime.fromisoformat(d.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported date type: {type(d)}")


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "bar_time", "open", "high", "low", "close", "adj_close", "volume", "source",
    ])
