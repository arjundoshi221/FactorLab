"""EODHD API client with rate limiting and API-key auth.

Usage:
    from factorlab.countries.us.equities.eodhd.client import EODHDClient
    client = EODHDClient()              # uses EODHD_API_KEY from .env
    bars = client.get_eod("AAPL.US")    # daily OHLCV
"""

import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests
from dotenv import find_dotenv, load_dotenv

from factorlab.core.secrets import get_secret

log = logging.getLogger(__name__)

BASE_URL = "https://eodhd.com/api"

# Rate-limit: 1000 req/min → ~16/sec safe ceiling.  We use 10/sec to be safe.
MIN_REQUEST_INTERVAL = 0.1  # 100ms between requests


class EODHDClient:
    """Thin, rate-limited wrapper around the EODHD REST API."""

    def __init__(self, api_key: str | None = None, *, storage=None, sleep=time.sleep):
        load_dotenv(find_dotenv(usecwd=True))
        self.api_key = api_key or get_secret("EODHD_API_KEY", "demo")
        self.session = requests.Session()
        self._last_request_at: float = 0.0
        self.storage = storage
        self.sleep = sleep
        self.last_raw_id = None

    # ── internals ────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            self.sleep(MIN_REQUEST_INTERVAL - elapsed)

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        """GET with rate limiting and API key injection."""
        url = f"{BASE_URL}{path}"
        safe_params = {"fmt": "json", **(params or {})}
        request_params = {"api_token": self.api_key, **safe_params}
        for attempt in range(5):
            self._throttle()
            try:
                resp = self.session.get(url, params=request_params, timeout=60)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 4:
                    raise
                self.sleep(2 ** attempt)
                continue
            self._last_request_at = time.monotonic()
            self.last_raw_id = None
            if self.storage is not None:
                self.last_raw_id = self.storage.archive_http_response(
                    source="eodhd", source_url=url, response_body=resp.content,
                    status_code=resp.status_code, response_headers=dict(resp.headers),
                    fetch_key=path, content_type=resp.headers.get("Content-Type", "application/json"),
                    metadata={"params": safe_params},
                )
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining and remaining.isdigit() and int(remaining) < 100:
                log.warning("EODHD rate limit low: %s remaining", remaining)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == 4:
                    resp.raise_for_status()
                delay = 2 ** attempt
                retry = resp.headers.get("Retry-After")
                if retry:
                    try:
                        delay = max(delay, float(retry))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                self.sleep(max(0, delay))
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("EODHD retry budget exhausted")

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

    def get_exchange_symbols(self, exchange: str = "US", *, instrument_type: str | None = None) -> list[dict]:
        """Fetch all symbols listed on an exchange.

        Returns list of dicts: Code, Name, Country, Exchange, Currency, Type, Isin.
        """
        params = {"type": instrument_type} if instrument_type else None
        data = self._get(f"/exchange-symbol-list/{exchange}", params).json()
        self.last_exchange_raw_id = self.last_raw_id
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

    def get_index_components(self, symbol: str) -> dict | list:
        """Fetch the current constituents for an EODHD index symbol."""
        data = self._get(f"/fundamentals/{symbol}", {"filter": "Components"}).json()
        size = len(data) if isinstance(data, (dict, list)) else 0
        log.info("EODHD index components %s: %d records", symbol, size)
        return data
