"""Schwab batch quote fetcher with structured DataFrame outputs.

A single ``get_quotes(symbols)`` call returns:
  - top-of-book (`quote` block, 28 keys)
  - reference data (`reference` block: cusip, exchange, isShortable, etc.)
  - inline fundamentals (`fundamental` block: peRatio, eps, divYield, sharesOutstanding, ...)
  - regular-session snapshot (`regular` block)
  - extended-hours snapshot (`extended` block)

This module flattens those into 3 DataFrames keyed by canonical symbol:
``quotes``, ``reference``, ``fundamentals``. Use ``fetch_quotes()`` for the
typed bundle; use the individual flatteners if you only need one slice.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from factorlab.countries.us.equities.schwab.symbols import from_schwab, to_schwab_batch

log = logging.getLogger(__name__)


@dataclass
class QuoteBundle:
    """Three flat DataFrames from one ``get_quotes`` batch call."""
    quotes: pd.DataFrame       # top-of-book + intraday OHLC + bid/ask + 52W
    reference: pd.DataFrame    # cusip, exchange, isShortable, htbRate, optionable
    fundamentals: pd.DataFrame # peRatio, eps, divYield, sharesOutstanding, ...
    invalid_symbols: list[str] # canonical symbols Schwab couldn't resolve
    fetched_at: datetime


def fetch_quotes(client, symbols: list[str]) -> QuoteBundle:
    """Fetch batch quotes for *symbols* (canonical form) and split into 3 DataFrames.

    Args:
        client: schwab-py client
        symbols: Canonical tickers (e.g. ``["AAPL", "BRK.B"]`` -- translated for API)

    Returns:
        QuoteBundle with separate DataFrames for quote / reference / fundamentals,
        plus the list of canonical symbols that failed to resolve.
    """
    schwab_syms = to_schwab_batch(symbols)
    fetched_at = datetime.now(timezone.utc)

    r = client.get_quotes(symbols=schwab_syms)
    if r.status_code != 200:
        raise RuntimeError(
            f"Schwab batch quote failed: HTTP {r.status_code} -- {r.text[:200]}"
        )

    payload = r.json()
    invalid_schwab = payload.pop("errors", {}).get("invalidSymbols", [])
    invalid = [from_schwab(s) for s in invalid_schwab]

    quote_rows = []
    ref_rows = []
    fund_rows = []
    for schwab_sym, blob in payload.items():
        canonical = from_schwab(schwab_sym)
        if not isinstance(blob, dict):
            continue

        q = blob.get("quote", {}) or {}
        quote_rows.append({
            "symbol": canonical,
            "asset_main_type": blob.get("assetMainType"),
            "quote_type": blob.get("quoteType"),
            "realtime": blob.get("realtime"),
            **q,
        })

        ref = blob.get("reference", {}) or {}
        ref_rows.append({"symbol": canonical, **ref})

        fund = blob.get("fundamental", {}) or {}
        fund_rows.append({"symbol": canonical, **fund})

    return QuoteBundle(
        quotes=pd.DataFrame(quote_rows),
        reference=pd.DataFrame(ref_rows),
        fundamentals=pd.DataFrame(fund_rows),
        invalid_symbols=invalid,
        fetched_at=fetched_at,
    )


def fetch_fundamentals_projection(client, symbols: list[str]) -> pd.DataFrame:
    """Pull the dedicated 56-field fundamentals projection.

    Returns one row per symbol with columns indexed to the canonical ticker.
    See ``docs/data-sources/us/schwab.md`` for the full field list.
    """
    from schwab.client.base import BaseClient

    schwab_syms = to_schwab_batch(symbols)
    r = client.get_instruments(
        schwab_syms,
        projection=BaseClient.Instrument.Projection.FUNDAMENTAL,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Schwab FUNDAMENTAL projection failed: HTTP {r.status_code} -- {r.text[:200]}"
        )

    payload = r.json()
    instruments = payload.get("instruments", [])

    rows = []
    for inst in instruments:
        canonical = from_schwab(inst.get("symbol", ""))
        fund = inst.get("fundamental", {}) or {}
        rows.append({
            "symbol": canonical,
            "cusip": inst.get("cusip"),
            "exchange": inst.get("exchange"),
            "asset_type": inst.get("assetType"),
            **fund,
        })
    return pd.DataFrame(rows)
