"""Schwab 1-min and 5-min intraday OHLCV fetcher.

Returns DataFrames with a ``session`` column (``pre`` | ``regular`` | ``post``)
so default factor pipelines can ``WHERE session='regular'`` while event-study
research drops the filter.

Quirks handled:
  - Each timestamp returned 2x (verified empirically 2026-05-01) -> dedup
  - ``need_extended_hours_data=False`` doesn't fully suppress pre/post bars,
    so we always tag session from the timestamp ourselves
  - Lookback ceilings: 1m ~45 days, 5m ~259 days; silent truncation if exceeded

Schema target: ``market_us.fact_equity`` with ``freq_id`` resolved from
``ref.frequencies`` (1m=id 1, 5m=id 2). The ``session`` column is set per-bar.
"""

import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from factorlab.countries.us.equities.schwab.symbols import to_schwab

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)

_FREQ_METHODS = {
    "1m": "get_price_history_every_minute",
    "5m": "get_price_history_every_five_minutes",
    "10m": "get_price_history_every_ten_minutes",
    "15m": "get_price_history_every_fifteen_minutes",
    "30m": "get_price_history_every_thirty_minutes",
}


def fetch_intraday(
    client,
    symbol: str,
    frequency: str = "1m",
    *,
    start_datetime: datetime | None = None,
    end_datetime: datetime | None = None,
) -> pd.DataFrame:
    """Fetch intraday OHLCV bars for *symbol* at *frequency*.

    Args:
        client: schwab-py client
        symbol: Canonical ticker (translated for the API via ``to_schwab``)
        frequency: ``"1m"``, ``"5m"``, ``"10m"``, ``"15m"``, or ``"30m"``
        start_datetime: Earliest bar (UTC). Default: 1 day ago
        end_datetime: Latest bar (UTC). Default: now

    Returns:
        DataFrame with columns:
        ``[bar_time, frequency, session, open, high, low, close, volume, source]``
        - ``bar_time``: tz-aware UTC pandas Timestamp
        - ``session``: ``pre`` | ``regular`` | ``post``
        - ``frequency``: passed through
        - ``source``: ``'schwab'``
        - **Deduped** on ``bar_time`` (Schwab returns each bar 2x)
        - Sorted ascending by ``bar_time``
    """
    if frequency not in _FREQ_METHODS:
        raise ValueError(f"frequency must be one of {list(_FREQ_METHODS)}, got {frequency!r}")
    method_name = _FREQ_METHODS[frequency]

    schwab_sym = to_schwab(symbol)
    fn = getattr(client, method_name)

    r = fn(
        symbol=schwab_sym,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        need_extended_hours_data=True,  # capture all sessions; we tag downstream
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Schwab {frequency} fetch failed for {symbol}: HTTP {r.status_code} -- {r.text[:200]}"
        )

    payload = r.json()
    if payload.get("empty", True):
        log.info("Schwab returned empty %s bars for %s", frequency, symbol)
        return _empty_frame()

    candles = payload.get("candles", [])
    if not candles:
        return _empty_frame()

    df = pd.DataFrame(candles)
    df["bar_time"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
    df = df.drop(columns=["datetime"])

    # Dedup -- Schwab returns each timestamp 2x
    df = df.drop_duplicates(subset="bar_time")

    # Tag session from ET timestamp
    et = df["bar_time"].dt.tz_convert(ET).dt.time
    df["session"] = pd.Series(
        ["regular"] * len(df), index=df.index, dtype="string"
    )
    df.loc[et < _RTH_OPEN, "session"] = "pre"
    df.loc[et >= _RTH_CLOSE, "session"] = "post"

    df["frequency"] = frequency
    df["source"] = "schwab"

    df = df[[
        "bar_time", "frequency", "session",
        "open", "high", "low", "close", "volume", "source",
    ]].sort_values("bar_time").reset_index(drop=True)
    return df


def filter_session(df: pd.DataFrame, session: str = "regular") -> pd.DataFrame:
    """Return rows of *df* matching *session* (``pre``/``regular``/``post``)."""
    return df[df["session"] == session].reset_index(drop=True)


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "bar_time", "frequency", "session",
        "open", "high", "low", "close", "volume", "source",
    ])
