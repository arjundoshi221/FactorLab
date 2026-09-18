"""Schwab data source -- US equities market data (read-only).

IMPORTANT: This package wraps ONLY market-data endpoints (``/marketdata/v1/*``).
Trading endpoints (``/trader/v1/*``) MUST NOT be called from this codebase.
See ``docs/data-sources/us/schwab.md`` ("Hard rule: no trading").

Public surface:
  - ``get_client(interactive=False)`` -- main entrypoint, returns schwab-py client
  - ``ensure_client(interactive=False)`` -- same, lower-level
  - ``login_interactive()`` -- force browser OAuth
  - ``validate_client(client)`` -- health check via AAPL quote
  - ``token_file_path()``, ``token_file_exists()``, ``delete_token()``

  - ``fetch_daily_bars(client, symbol, from_date, to_date)`` -- daily OHLCV
  - ``fetch_intraday(client, symbol, frequency, start, end)`` -- 1m/5m bars with session tag
  - ``filter_session(df, session)`` -- helper to slice intraday by session

  - ``fetch_quotes(client, symbols) -> QuoteBundle`` -- batch quote + reference + fundamentals
  - ``fetch_fundamentals_projection(client, symbols)`` -- 56-field deep fundamentals

  - ``to_schwab(sym) / from_schwab(sym)`` -- BRK.B <-> BRK/B normalization
"""

from factorlab.countries.us.equities.schwab.auth import (
    delete_token,
    ensure_client,
    login_interactive,
    token_file_exists,
    token_file_path,
    validate_client,
)
from factorlab.countries.us.equities.schwab.candles import fetch_daily_bars
from factorlab.countries.us.equities.schwab.client import get_client
from factorlab.countries.us.equities.schwab.intraday import fetch_intraday, filter_session
from factorlab.countries.us.equities.schwab.quotes import (
    QuoteBundle,
    fetch_fundamentals_projection,
    fetch_quotes,
)
from factorlab.countries.us.equities.schwab.symbols import (
    from_schwab,
    from_schwab_batch,
    to_schwab,
    to_schwab_batch,
)

__all__ = [
    # auth
    "delete_token", "ensure_client", "get_client", "login_interactive",
    "token_file_exists", "token_file_path", "validate_client",
    # data fetchers
    "fetch_daily_bars", "fetch_intraday", "filter_session",
    "fetch_quotes", "fetch_fundamentals_projection", "QuoteBundle",
    # symbols
    "to_schwab", "from_schwab", "to_schwab_batch", "from_schwab_batch",
]
