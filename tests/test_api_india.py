from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

from factorlab.api.app import app, get_india_candles_repository
from factorlab.api.india import (
    IndiaCandlesRepository,
    decode_cursor,
    decode_instrument_cursor,
)

COLUMNS = [
    "instrument_id", "contract_id", "symbol", "market_code", "bar_time",
    "open", "high", "low", "close", "volume", "oi", "source",
    "as_of_time", "ingested_at",
]
INSTRUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
CONTRACT_ID = UUID(int=0)


def candle_row(minute: int) -> tuple:
    timestamp = datetime(2026, 8, 12, 9, minute, tzinfo=UTC)
    return (
        INSTRUMENT_ID, CONTRACT_ID, "RELIANCE", "IND", timestamp,
        Decimal("100.000000"), Decimal("101.000000"), Decimal("99.000000"),
        Decimal("100.500000"), 1000, None, "upstox", timestamp, timestamp,
    )


class QueryResult:
    def __init__(self, rows, column_names=COLUMNS):
        self.result_rows = rows
        self.column_names = column_names


class FakeQueryClient:
    def __init__(self, rows, column_names=COLUMNS):
        self.rows = rows
        self.column_names = column_names
        self.calls = []

    def query(self, query, parameters=None):
        self.calls.append((query, parameters))
        return QueryResult(self.rows, self.column_names)


def test_repository_filters_and_paginates_candles():
    client = FakeQueryClient([candle_row(3), candle_row(2), candle_row(1)])
    repository = IndiaCandlesRepository(client)
    page = repository.list_candles(
        instrument_id=INSTRUMENT_ID,
        symbol="reliance",
        trading_date=date(2026, 8, 12),
        source="upstox",
        limit=2,
    )

    query, parameters = client.calls[0]
    assert "FROM market_candles_1min FINAL" in query
    assert parameters["symbol"] == "RELIANCE"
    assert parameters["instrument_id"] == INSTRUMENT_ID
    assert parameters["trading_date"] == date(2026, 8, 12)
    assert "toDate(bar_time, 'Asia/Kolkata') = {trading_date:Date}" in query
    assert parameters["source"] == "upstox"
    assert parameters["fetch_limit"] == 3
    assert len(page.items) == 2
    assert page.next_cursor is not None
    decoded = decode_cursor(page.next_cursor)
    assert decoded["cursor_time"] == datetime(2026, 8, 12, 9, 2, tzinfo=UTC)
    assert decoded["cursor_instrument"] == INSTRUMENT_ID


def test_repository_lists_and_paginates_reference_instruments():
    columns = [
        "instrument_id", "instrument_key", "trading_symbol", "name", "isin",
        "exchange_code", "segment", "instrument_type", "asset_class", "currency_code",
        "lot_size", "tick_size", "status", "source", "first_seen", "last_seen",
        "ingested_at",
    ]
    timestamp = datetime(2026, 8, 12, tzinfo=UTC)
    rows = [
        (
            UUID(int=index), f"NSE_EQ|{index}", symbol, name, f"INE{index:09d}",
            "NSE", "NSE_EQ", "EQ", "equity", "INR", 1, Decimal("0.050000"),
            "active", "upstox", date(2026, 8, 1), date(2026, 8, 12), timestamp,
        )
        for index, symbol, name in [
            (1, "INFY", "Infosys"),
            (2, "RELIANCE", "Reliance Industries"),
            (3, "TCS", "Tata Consultancy Services"),
        ]
    ]
    client = FakeQueryClient(rows, columns)
    page = IndiaCandlesRepository(client).list_instruments(
        search="rel", status="active", source="upstox", limit=2
    )

    query, parameters = client.calls[0]
    assert "FROM ref_instruments FINAL" in query
    assert "positionCaseInsensitiveUTF8" in query
    assert parameters == {
        "fetch_limit": 3, "search": "rel", "status": "active", "source": "upstox"
    }
    assert [item.trading_symbol for item in page.items] == ["INFY", "RELIANCE"]
    assert decode_instrument_cursor(page.next_cursor) == ("RELIANCE", UUID(int=2))


def test_repository_returns_overall_and_daily_stats():
    timestamp = datetime(2026, 8, 12, 10, tzinfo=UTC)
    overall_columns = [
        "reference_instruments", "instruments_with_data", "unique_series", "data_points",
        "trading_days", "first_bar_time", "last_bar_time", "data_as_of",
    ]
    overall_client = FakeQueryClient(
        [(2464, 5, 10, 3750, 1, timestamp, timestamp, timestamp)],
        overall_columns,
    )
    stats = IndiaCandlesRepository(overall_client).get_stats(
        date_from=date(2026, 8, 12), date_to=date(2026, 8, 12), source="upstox"
    )

    query, parameters = overall_client.calls[0]
    assert "uniqExact(instrument_id)" in query
    assert "uniqExact(tuple(instrument_id, contract_id))" in query
    assert "toDate(bar_time, 'Asia/Kolkata')" in query
    assert parameters["date_from"] == date(2026, 8, 12)
    assert stats.reference_instruments == 2464
    assert stats.data_points == 3750

    daily_columns = [
        "trading_date", "data_points", "unique_instruments", "unique_series",
        "first_bar_time", "last_bar_time", "data_as_of",
    ]
    daily_client = FakeQueryClient(
        [(date(2026, 8, 12), 3750, 5, 10, timestamp, timestamp, timestamp)],
        daily_columns,
    )
    page = IndiaCandlesRepository(daily_client).list_daily_stats(limit=30)
    assert page.limit == 30
    assert page.items[0].unique_series == 10
    assert daily_client.calls[0][1]["limit"] == 30


class FakeRepository:
    def __init__(self):
        self.kwargs = None

    def list_candles(self, **kwargs):
        self.kwargs = kwargs
        return {"items": [], "next_cursor": None, "limit": kwargs["limit"], "data_as_of": None}

    def list_instruments(self, **kwargs):
        self.kwargs = kwargs
        return {"items": [], "next_cursor": None, "limit": kwargs["limit"], "data_as_of": None}

    def get_stats(self, **kwargs):
        self.kwargs = kwargs
        return {
            "reference_instruments": 2464,
            "instruments_with_data": 5,
            "unique_series": 10,
            "data_points": 3750,
            "trading_days": 1,
            "first_bar_time": None,
            "last_bar_time": None,
            "data_as_of": None,
        }

    def list_daily_stats(self, **kwargs):
        self.kwargs = kwargs
        return {"items": [], "limit": kwargs["limit"]}


def test_endpoint_requires_auth_and_forwards_filters(monkeypatch):
    repository = FakeRepository()
    app.dependency_overrides[get_india_candles_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/india/candles/1min")
            response = client.get(
                "/api/v1/india/candles/1min",
                params={"symbol": "RELIANCE", "source": "upstox", "limit": 25},
                headers={"Authorization": "Bearer test-secret"},
            )
            api_kwargs = repository.kwargs.copy()
            hub_response = client.get(
                f"/hub/api/v1/india/instruments/{INSTRUMENT_ID}/candles",
                params={"trading_date": "2026-08-12", "limit": 500},
            )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert hub_response.status_code == 200
    assert api_kwargs["symbol"] == "RELIANCE"
    assert api_kwargs["limit"] == 25
    assert repository.kwargs["instrument_id"] == INSTRUMENT_ID
    assert repository.kwargs["trading_date"] == date(2026, 8, 12)


def test_endpoint_rejects_reversed_time_range(monkeypatch):
    repository = FakeRepository()
    app.dependency_overrides[get_india_candles_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/india/candles/1min",
                params={"time_from": "2026-08-12T10:00:00Z", "time_to": "2026-08-12T09:00:00Z"},
                headers={"Authorization": "Bearer test-secret"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert repository.kwargs is None


def test_generic_india_endpoints_require_auth_and_forward_filters(monkeypatch):
    repository = FakeRepository()
    app.dependency_overrides[get_india_candles_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    headers = {"Authorization": "Bearer test-secret"}
    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/india/instruments")
            instruments = client.get(
                "/api/v1/india/instruments",
                params={"search": "tata", "status": "active", "limit": 25},
                headers=headers,
            )
            stats = client.get(
                "/api/v1/india/stats",
                params={"date_from": "2026-08-01", "source": "upstox"},
                headers=headers,
            )
            daily = client.get(
                "/api/v1/india/stats/daily",
                params={"date_to": "2026-08-12", "limit": 30},
                headers=headers,
            )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert instruments.status_code == 200
    assert stats.status_code == 200
    assert daily.status_code == 200
    assert repository.kwargs["date_to"] == date(2026, 8, 12)
    assert repository.kwargs["limit"] == 30


def test_stats_endpoints_reject_reversed_date_range(monkeypatch):
    repository = FakeRepository()
    app.dependency_overrides[get_india_candles_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/india/stats",
                params={"date_from": "2026-08-12", "date_to": "2026-08-01"},
                headers={"Authorization": "Bearer test-secret"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert repository.kwargs is None
