"""EODHD API client with rate limiting and API-key auth.

Usage:
    from factorlab.sources.eodhd.client import EODHDClient
    client = EODHDClient()              # uses EODHD_API_KEY from .env
    bars = client.get_eod("AAPL.US")    # daily OHLCV
"""

import logging
import os
import time

import requests
from dotenv import find_dotenv, load_dotenv

log = logging.getLogger(__name__)

BASE_URL = "https://eodhd.com/api"

# Rate-limit: 1000 req/min → ~16/sec safe ceiling.  We use 10/sec to be safe.
MIN_REQUEST_INTERVAL = 0.1  # 100ms between requests


class EODHDClient:
    """Thin, rate-limited wrapper around the EODHD REST API."""

    def __init__(self, api_key: str | None = None):
        load_dotenv(find_dotenv(usecwd=True))
        self.api_key = api_key or os.getenv("EODHD_API_KEY", "demo")
        self.session = requests.Session()
        self._last_request_at: float = 0.0

    # ── internals ────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        """GET with rate limiting and API key injection."""
        self._throttle()
        url = f"{BASE_URL}{path}"
        p = {"api_token": self.api_key, "fmt": "json"}
        if params:
            p.update(params)
        resp = self.session.get(url, params=p, timeout=30)
        self._last_request_at = time.monotonic()

        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining and int(remaining) < 100:
            log.warning("EODHD rate limit low: %s remaining", remaining)

        resp.raise_for_status()
        return resp

    # ── public API ───────────────────────────────────────────────────────

    def get_eod(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        """Fetch daily OHLCV bars for a symbol.

        Args:
            symbol: EODHD format, e.g. 'AAPL.US'
            from_date: 'YYYY-MM-DD' (optional)
            to_date: 'YYYY-MM-DD' (optional)

        Returns:
            List of dicts: date, open, high, low, close, adjusted_close, volume
        """
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._get(f"/eod/{symbol}", params).json()
        log.info("EODHD eod %s: %d bars", symbol, len(data))
        return data

    def get_exchange_symbols(self, exchange: str = "US") -> list[dict]:
        """Fetch all symbols listed on an exchange.

        Returns list of dicts: Code, Name, Country, Exchange, Currency, Type, Isin.
        """
        data = self._get(f"/exchange-symbol-list/{exchange}").json()
        log.info("EODHD exchange-symbol-list/%s: %d symbols", exchange, len(data))
        return data

    def get_bulk_eod(self, exchange: str = "US", date: str | None = None) -> list[dict]:
        """Fetch last-day bulk EOD for an entire exchange (1 API call, costs 100)."""
        params = {}
        if date:
            params["date"] = date
        data = self._get(f"/eod-bulk-last-day/{exchange}", params).json()
        log.info("EODHD bulk-eod/%s: %d rows", exchange, len(data))
        return data

    def get_dividends(self, symbol: str, from_date: str | None = None) -> list[dict]:
        """Fetch dividend history for a symbol."""
        params = {}
        if from_date:
            params["from"] = from_date
        return self._get(f"/div/{symbol}", params).json()

    def get_splits(self, symbol: str, from_date: str | None = None) -> list[dict]:
        """Fetch split history for a symbol."""
        params = {}
        if from_date:
            params["from"] = from_date
        return self._get(f"/splits/{symbol}", params).json()

    def get_fundamentals(self, symbol: str) -> dict:
        """Fetch full fundamentals dump (costs 10 API calls)."""
        return self._get(f"/fundamentals/{symbol}").json()
