"""EODHD data source — US equities daily OHLCV, fundamentals, corporate actions."""

from factorlab.sources.eodhd.candles import fetch_daily_bars, fetch_demo_universe
from factorlab.sources.eodhd.client import EODHDClient
from factorlab.sources.eodhd.instruments import fetch_us_instruments, sync_us_instruments

__all__ = [
    "EODHDClient",
    "fetch_daily_bars",
    "fetch_demo_universe",
    "fetch_us_instruments",
    "sync_us_instruments",
]
