from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from factorlab.api.app import app, get_political_trades_repository
from factorlab.api.political import PoliticalTradesRepository, decode_cursor

COLUMNS = [
    "trade_key",
    "political_trade_id",
    "listing_id",
    "contract_id",
    "legislator_entity_id",
    "chamber",
    "filing_id",
    "filing_date",
    "filing_url",
    "bioguide_id",
    "legislator_name",
    "state",
    "district",
    "owner_code",
    "filer_type",
    "asset_name_raw",
    "ticker",
    "asset_type_code",
    "transaction_type",
    "transaction_date",
    "notification_date",
    "amount_str",
    "amount_min",
    "amount_max",
    "source",
    "as_of_time",
    "ingested_at",
]


def trade_row(day: int, key_character: str = "a") -> tuple:
    timestamp = datetime(2026, 8, day, 12, tzinfo=UTC)
    return (
        "00000000-0000-0000-0000-00000000000" + str(day),
        UUID("00000000-0000-0000-0000-00000000000" + str(day)),
        None,
        None,
        None,
        "house",
        f"filing-{day}",
        date(2026, 8, day),
        f"https://example.test/{day}",
        "P000197",
        "Nancy Pelosi",
        "CA",
        11,
        "SP",
        "self",
        "Example Corp (EXM)",
        "EXM",
        "ST",
        "purchase",
        date(2026, 8, day),
        None,
        "$1,001 - $15,000",
        1001,
        15000,
        "house_clerk_ptr",
        timestamp,
        timestamp,
    )


class QueryResult:
    column_names = COLUMNS

    def __init__(self, rows):
        self.result_rows = rows


class FakeQueryClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, query, parameters=None):
        self.calls.append((query, parameters))
        return QueryResult(self.rows)


def test_repository_filters_and_returns_cursor_page():
    client = FakeQueryClient([trade_row(3, "c"), trade_row(2, "b"), trade_row(1, "a")])
    repository = PoliticalTradesRepository(client)

    page = repository.list_trades(
        ticker="exm",
        bioguide_id="p000197",
        chamber="house",
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 3),
        limit=2,
    )

    query, parameters = client.calls[0]
    assert "FROM alt.political_trades AS t FINAL" in query
    assert "ORDER BY transaction_date DESC, political_trade_id DESC" in query
    assert parameters["ticker"] == "EXM"
    assert parameters["bioguide_id"] == "P000197"
    assert parameters["fetch_limit"] == 3
    assert len(page.items) == 2
    assert page.next_cursor is not None
    assert decode_cursor(page.next_cursor) == (
        date(2026, 8, 2), UUID("00000000-0000-0000-0000-000000000002")
    )
    assert page.data_as_of == datetime(2026, 8, 3, 12, tzinfo=UTC)


def test_legacy_political_cursor_is_rejected_clearly():
    import base64
    import json

    from fastapi import HTTPException

    legacy = base64.urlsafe_b64encode(json.dumps({"date": "2026-08-02", "trade_key": "a" * 64}).encode()).decode()
    with pytest.raises(HTTPException, match="Legacy cursor"):
        decode_cursor(legacy)


class FakeRepository:
    def __init__(self):
        self.kwargs = None

    def list_trades(self, **kwargs):
        self.kwargs = kwargs
        return {
            "items": [],
            "next_cursor": None,
            "limit": kwargs["limit"],
            "data_as_of": None,
        }


def test_endpoint_requires_auth_and_forwards_valid_filters(monkeypatch):
    repository = FakeRepository()
    app.dependency_overrides[get_political_trades_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/political/trades")
            response = client.get(
                "/api/v1/political/trades",
                params={
                    "ticker": "EXM",
                    "bioguide_id": "P000197",
                    "chamber": "house",
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-03",
                    "limit": 25,
                },
                headers={"Authorization": "Bearer test-secret"},
            )
            api_kwargs = repository.kwargs.copy()
            hub_response = client.get(
                "/hub/api/v1/political/trades",
                params={"ticker": "EXM", "chamber": "house", "limit": 25},
            )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert hub_response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None, "limit": 25, "data_as_of": None}
    assert api_kwargs["ticker"] == "EXM"
    assert api_kwargs["date_from"] == date(2026, 8, 1)


def test_endpoint_rejects_reversed_date_range(monkeypatch):
    repository = FakeRepository()
    app.dependency_overrides[get_political_trades_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/political/trades?date_from=2026-08-03&date_to=2026-08-01",
                headers={"Authorization": "Bearer test-secret"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert repository.kwargs is None
