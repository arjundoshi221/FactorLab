"""Index values ($SPX, ...) validate through quotes and instrument lookups."""

import json
from datetime import UTC, datetime

from factorlab.ingest.provider import RawCapture
from factorlab.sources.schwab.normalize import normalize_instrument, normalize_quote_listings
from factorlab.sources.schwab.settings import DEFAULT_EXCHANGES, INDEX_VENUE

NOW = datetime(2026, 10, 21, 21, tzinfo=UTC)


def capture(payload, **metadata):
    return RawCapture(json.dumps(payload).encode(), "test", "http", NOW, metadata=metadata)


def test_quote_batch_accepts_indexes_and_equities():
    payload = {
        "$SPX": {
            "assetMainType": "INDEX",
            "symbol": "$SPX",
            "reference": {"description": "S&P 500 INDEX", "exchangeName": "Index"},
        },
        "XLK": {
            "assetMainType": "EQUITY",
            "assetSubType": "ETF",
            "symbol": "XLK",
            "reference": {"exchangeName": "NYSE Arca", "currency": "USD"},
        },
    }
    rows = normalize_quote_listings(
        capture(payload, endpoint="quotes", symbols=["$SPX", "XLK"]), exchanges=DEFAULT_EXCHANGES
    )
    by_symbol = {row.ref.trading_symbol: row for row in rows}
    spx = by_symbol["$SPX"]
    assert (spx.product_type, spx.ref.exchange_code, spx.ref.alias_value) == (
        "index",
        INDEX_VENUE,
        "$SPX",
    )
    assert spx.name == "S&P 500 INDEX"
    # ETFs keep the quote path's product type: it is part of the market.bars key.
    assert by_symbol["XLK"].product_type == "common"


def test_quote_batch_still_rejects_other_asset_types():
    payload = {"/ES": {"assetMainType": "FUTURE", "symbol": "/ES", "reference": {}}}
    rows = normalize_quote_listings(
        capture(payload, endpoint="quotes", symbols=[]), exchanges=DEFAULT_EXCHANGES
    )
    assert rows == []


def test_instrument_lookup_maps_index():
    payload = {
        "instruments": [
            {
                "symbol": "$COMPX",
                "description": "NASDAQ COMPOSITE",
                "exchange": "Index",
                "assetType": "INDEX",
            }
        ]
    }
    [row] = normalize_instrument(capture(payload, symbol="$COMPX"), exchanges=DEFAULT_EXCHANGES)
    assert (row.product_type, row.ref.exchange_code, row.ref.isin) == ("index", INDEX_VENUE, None)


def test_quote_fundamentals_yield_shares_outstanding():
    from factorlab.sources.schwab.normalize import normalize_quote_shares

    # Shape per docs/data-sources/us/schwab.md (quote `fundamental` block, verified 2026-05-01).
    payload = {
        "AAPL": {
            "assetMainType": "EQUITY",
            "symbol": "AAPL",
            "fundamental": {"sharesOutstanding": 1.4697926e10, "peRatio": 30.1},
        },
        "BRK/B": {"assetMainType": "EQUITY", "symbol": "BRK/B", "fundamental": {}},
        "$SPX": {"assetMainType": "INDEX", "symbol": "$SPX", "fundamental": {}},
    }
    [row] = normalize_quote_shares(
        capture(payload, endpoint="quotes", symbols=["AAPL", "BRK/B", "$SPX"])
    )
    assert (row.instrument.alias_value, row.shares_outstanding) == ("AAPL", 14_697_926_000)
    assert row.as_of.isoformat() == "2026-10-21"
