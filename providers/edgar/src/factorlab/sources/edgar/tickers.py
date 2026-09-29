"""Ticker ↔ CIK resolver from SEC's company_tickers.json.

The SEC publishes an authoritative map of every listed filer's ticker and CIK.
Cached in-process after first fetch.

    from factorlab.sources.edgar import EdgarClient, resolve_cik
    client = EdgarClient()
    cik = resolve_cik(client, "AAPL")   # → 320193
"""

from __future__ import annotations

from typing import Any

from factorlab.sources.edgar.client import EdgarClient

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_TICKER_EXCHANGE_MAP_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

_cache: dict[str, dict[str, Any]] = {}


def get_ticker_cik_map(
    client: EdgarClient,
    *,
    include_exchange: bool = False,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return a dict keyed by uppercased ticker → {cik, name[, exchange]}.

    In-process cached. Set ``force_refresh=True`` to re-fetch.
    """
    cache_key = "with_exchange" if include_exchange else "basic"
    if not force_refresh and cache_key in _cache:
        return _cache[cache_key]

    if include_exchange:
        raw = client.get(_TICKER_EXCHANGE_MAP_URL).json()
        fields: list[str] = raw.get("fields", [])
        data: list[list[Any]] = raw.get("data", [])
        ticker_idx = fields.index("ticker") if "ticker" in fields else 2
        cik_idx = fields.index("cik") if "cik" in fields else 0
        name_idx = fields.index("name") if "name" in fields else 1
        exch_idx = fields.index("exchange") if "exchange" in fields else 3
        out: dict[str, dict[str, Any]] = {}
        for row in data:
            ticker = str(row[ticker_idx]).upper()
            out[ticker] = {
                "cik": int(row[cik_idx]),
                "name": row[name_idx],
                "exchange": row[exch_idx] if exch_idx < len(row) else None,
            }
    else:
        raw = client.get(_TICKER_MAP_URL).json()
        # Shape: { "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ... }
        out = {
            str(entry["ticker"]).upper(): {
                "cik": int(entry["cik_str"]),
                "name": entry["title"],
            }
            for entry in raw.values()
        }

    _cache[cache_key] = out
    return out


def resolve_cik(client: EdgarClient, ticker: str) -> int | None:
    """Look up a CIK by ticker. Returns ``None`` if unknown."""
    entry = get_ticker_cik_map(client).get(ticker.upper())
    return entry["cik"] if entry else None
