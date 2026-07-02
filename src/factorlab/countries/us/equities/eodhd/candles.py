"""Fetch EODHD daily bars and convert to DataFrame / DB-ready format.

Maps EODHD response → market.candles_daily schema.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

import pandas as pd

from factorlab.sources.eodhd.client import EODHDClient

log = logging.getLogger(__name__)


def fetch_daily_bars(
    client: EODHDClient,
    symbol: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> pd.DataFrame:
    """Fetch daily OHLCV for one symbol and return a clean DataFrame.

    Columns: trade_date, open, high, low, close, adj_close, volume, source
    """
    raw = client.get_eod(symbol, from_date=from_date, to_date=to_date)
    if not raw:
        log.warning("No bars returned for %s", symbol)
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    df = df.rename(columns={
        "date": "trade_date",
        "adjusted_close": "adj_close",
    })
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df["source"] = "eodhd"

    # Keep only canonical columns
    cols = ["trade_date", "open", "high", "low", "close", "adj_close", "volume", "source"]
    df = df[[c for c in cols if c in df.columns]]

    log.info("%s: %d daily bars (%s → %s)", symbol, len(df),
             df["trade_date"].iloc[0] if len(df) else "?",
             df["trade_date"].iloc[-1] if len(df) else "?")
    return df


def fetch_demo_universe(
    client: EODHDClient,
    symbols: list[str] | None = None,
    from_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch daily bars for a list of symbols. Returns {symbol: DataFrame}.

    Default symbols: AAPL.US, TSLA.US, AMZN.US, VTI.US (free demo tickers).
    """
    if symbols is None:
        symbols = ["AAPL.US", "TSLA.US", "AMZN.US", "VTI.US"]

    results = {}
    for sym in symbols:
        try:
            df = fetch_daily_bars(client, sym, from_date=from_date)
            if not df.empty:
                results[sym] = df
        except Exception as e:
            log.error("Failed to fetch %s: %s", sym, e)

    log.info("Fetched %d/%d symbols", len(results), len(symbols))
    return results
