from datetime import UTC, date, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from factorlab.components.api.app import app
from factorlab.components.api.market_data import MarketDataRepository, repository


def test_configured_universe_includes_tickers_without_bars_and_searches_names():
    ident = uuid4()
    repo = MarketDataRepository(Mock())
    repo.query = Mock(
        side_effect=[
            [{"configured_total": 2, "configuration_as_of": datetime(2026, 10, 14, tzinfo=UTC)}],
            [{"total": 1}],
            [
                {
                    "listing_id": ident,
                    "symbol": "AAPL.US",
                    "factorlab_symbol": "AAPL",
                    "name": "Apple Inc.",
                    "source": "schwab",
                    "universe": "russell3000",
                    "series_count": 1,
                }
            ],
            [],
            [],
        ]
    )
    page = repo.list_universe(
        market="US", stream="daily", search="apple", data_filter="no_data", limit=50, offset=0
    )
    assert page.configured_total == 2
    assert page.items[0].symbol == "AAPL.US"
    assert page.items[0].factorlab_symbol == "AAPL"
    assert page.items[0].rows == 0
    sql = repo.query.call_args_list[1].args[0]
    assert "meta.expected_series FINAL" in sql
    assert "LEFT JOIN ref.listings AS l FINAL ON l.listing_id = c.listing_id" in sql
    assert "positionCaseInsensitiveUTF8(ifNull(e.legal_name" in sql
    assert "positionCaseInsensitiveUTF8(ifNull(l.trading_symbol" in sql
    assert "l.trading_symbol AS factorlab_symbol" in repo.query.call_args_list[2].args[0]
    assert "NOT IN" in sql
    assert "b.trade_date >= {cutoff:Date}" in sql
    assert repo.query.call_args_list[1].kwargs["search"] == "apple"


def test_ticker_detail_counts_market_sessions_missing_from_stored_range():
    ident = uuid4()
    repo = MarketDataRepository(Mock())
    repo.query = Mock(
        side_effect=[
            [
                {
                    "listing_id": ident,
                    "symbol": "AAPL.US",
                    "source": "schwab",
                    "universe": "russell3000",
                    "series_count": 1,
                }
            ],
            [{"name": "Apple Inc.", "factorlab_symbol": "AAPL"}],
            [
                {
                    "listing_id": ident,
                    "rows": 2,
                    "trading_days": 2,
                    "first_date": date(2026, 10, 12),
                    "latest_date": date(2026, 10, 14),
                    "last_ingested_at": datetime(2026, 10, 15, tzinfo=UTC),
                }
            ],
            [{"listing_id": ident, "history_complete": False, "recovery_error": ""}],
            [{"trade_date": date(2026, 10, 12)}, {"trade_date": date(2026, 10, 14)}],
        ]
    )
    detail = repo.ticker("US", "historical", ident)
    assert detail.rows == 2
    assert detail.symbol == "AAPL.US"
    assert detail.factorlab_symbol == "AAPL"
    assert detail.missing_trading_days == 1
    assert detail.history_complete is False
    assert detail.gap_window_from == date(2026, 10, 12)


def test_india_equity_and_future_keep_separate_counts():
    listing = uuid4()
    contract = uuid4()
    repo = MarketDataRepository(Mock())
    repo.query = Mock(
        side_effect=[
            [
                {
                    "listing_id": listing,
                    "contract_id": None,
                    "rows": 100,
                    "trading_days": 1,
                    "first_date": date(2026, 10, 14),
                    "latest_date": date(2026, 10, 14),
                    "last_ingested_at": None,
                },
                {
                    "listing_id": listing,
                    "contract_id": contract,
                    "rows": 20,
                    "trading_days": 1,
                    "first_date": date(2026, 10, 14),
                    "latest_date": date(2026, 10, 14),
                    "last_ingested_at": None,
                },
            ],
            [{"listing_id": listing, "history_complete": True, "recovery_error": ""}],
        ]
    )
    entries = [
        {
            "listing_id": listing,
            "contract_id": None,
            "symbol": "TCS",
            "name": "TCS",
            "source": "upstox",
            "universe": "full_nse_eq",
            "series_count": 1,
        },
        {
            "listing_id": listing,
            "contract_id": contract,
            "symbol": "TCS FUT",
            "name": "TCS",
            "source": "upstox",
            "universe": "full_nse_eq",
            "series_count": 1,
        },
    ]
    items = repo._coverage("IN", "historical", "1min", entries)
    assert [item.rows for item in items] == [100, 20]
    assert items[1].contract_id == contract
    assert items[0].history_complete is True
    assert items[1].history_complete is None
    assert "market.futures_contract_bars" in repo.query.call_args_list[0].args[0]


def test_contract_detail_is_scoped_to_the_selected_future():
    contract = uuid4()
    repo = MarketDataRepository(Mock())
    repo.query = Mock(return_value=[])
    with pytest.raises(HTTPException) as error:
        repo.ticker("IN", "historical", uuid4(), contract)
    assert error.value.status_code == 404
    assert "AND contract_id = {contract_id:UUID}" in repo.query.call_args.args[0]
    assert repo.query.call_args.kwargs["contract_id"] == contract


def test_hub_routes_forward_filters_and_validate_market():
    repo = Mock()
    repo.list_universe.return_value = {
        "market": "IN",
        "stream": "historical",
        "configured_total": 0,
        "total": 0,
        "limit": 50,
        "offset": 0,
        "configuration_as_of": None,
        "items": [],
    }
    app.dependency_overrides[repository] = lambda: repo
    try:
        client = TestClient(app)
        response = client.get(
            "/hub/api/v1/market-data/universe",
            params={
                "market": "IN",
                "stream": "historical",
                "search": "  TCS  ",
                "data_filter": "has_data",
            },
        )
        invalid = client.get("/hub/api/v1/market-data/universe?market=UK")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert invalid.status_code == 422
    assert repo.list_universe.call_args.kwargs["search"] == "TCS"
    assert repo.list_universe.call_args.kwargs["data_filter"] == "has_data"
