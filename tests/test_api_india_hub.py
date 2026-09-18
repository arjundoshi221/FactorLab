from datetime import UTC, date, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from factorlab.api.app import app, get_india_hub_repository
from factorlab.api.india_hub import IndiaHubRepository

INSTRUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
SECOND_INSTRUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
TRADING_DATE = date(2026, 8, 12)
NOW = datetime(2026, 8, 12, 11, 0, tzinfo=UTC)


class QueryResult:
    def __init__(self, columns, rows):
        self.column_names = columns
        self.result_rows = rows


class QueueQueryClient:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def query(self, query, parameters=None):
        self.calls.append((query, parameters))
        return self.results.pop(0)


def result(columns, *rows):
    return QueryResult(columns, list(rows))


def test_india_hub_repository_dependency_is_request_local(monkeypatch):
    created = []

    def create_repository(*_args):
        repository = object()
        created.append(repository)
        return repository

    monkeypatch.setattr(IndiaHubRepository, "from_environment", create_repository)

    assert get_india_hub_repository() is not get_india_hub_repository()
    assert len(created) == 2


INSTRUMENT_COLUMNS = [
    "instrument_id",
    "symbol",
    "name",
    "exchange_code",
    "segment",
    "instrument_type",
    "status",
    "source",
    "data_points",
    "trading_days",
    "unique_series",
    "first_bar_time",
    "last_bar_time",
    "last_ingested_at",
    "expected_series",
    "selected_data_points",
    "selected_unique_series",
    "ohlc_violations",
    "null_ohlc_values",
    "outside_session",
    "duplicate_versions",
    "total",
]

SUMMARY_COLUMNS = [
    "reference_total",
    "collecting_total",
    "historical_total",
    "not_configured_total",
    "data_as_of",
]


def test_instrument_page_combines_unique_history_and_selected_day_checks():
    client = QueueQueryClient(
        result(SUMMARY_COLUMNS, (2684, 2, 0, 2682, NOW)),
        result(
            INSTRUMENT_COLUMNS,
            (
                INSTRUMENT_ID,
                "TCS",
                "Tata Consultancy Services",
                "NSE",
                "NSE_EQ",
                "EQ",
                "active",
                "upstox",
                7500,
                10,
                2,
                NOW,
                NOW,
                NOW,
                2,
                750,
                2,
                0,
                0,
                0,
                100,
                2,
            ),
            (
                SECOND_INSTRUMENT_ID,
                "INFY",
                "Infosys",
                "NSE",
                "NSE_EQ",
                "EQ",
                "active",
                "upstox",
                3750,
                10,
                1,
                NOW,
                NOW,
                NOW,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
                2,
            ),
        )
    )

    page = IndiaHubRepository(client).list_instruments(
        trading_date=TRADING_DATE,
        search="t",
        now=NOW,
    )

    assert page.total == 2
    assert page.reference_total == 2684
    assert page.collecting_total == 2
    assert page.not_configured_total == 2682
    assert page.items[0].collection_status == "collecting"
    assert page.items[0].check_status == "healthy"
    assert page.items[0].coverage_percent == 100
    assert page.items[1].check_status == "missing"
    query, parameters = client.calls[1]
    assert "count() OVER () AS total" in query
    assert "market_candles_1min FINAL" in query
    assert "FROM reference" in query
    assert parameters["search"] == "t"


def test_instrument_page_can_select_unconfigured_reference_instruments():
    client = QueueQueryClient(
        result(SUMMARY_COLUMNS, (2684, 5, 0, 2679, NOW)),
        result(
            INSTRUMENT_COLUMNS,
            (
                SECOND_INSTRUMENT_ID,
                "AARTIDRUGS",
                "Aarti Drugs Limited",
                "NSE",
                "NSE_EQ",
                "EQ",
                "active",
                "upstox",
                0,
                0,
                0,
                None,
                None,
                None,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                2679,
            ),
        ),
    )

    page = IndiaHubRepository(client).list_instruments(
        trading_date=TRADING_DATE,
        scope="not_configured",
        now=NOW,
    )

    assert page.scope == "not_configured"
    assert page.total == 2679
    assert page.items[0].collection_status == "not_configured"
    assert page.items[0].check_status == "not_expected"
    assert page.items[0].data_points == 0
    query, _ = client.calls[1]
    assert "ifNull(history.data_points, 0) = 0" in query


def test_instrument_days_includes_missing_exchange_sessions():
    columns = [
        "trading_date",
        "data_points",
        "unique_series",
        "first_bar_time",
        "last_bar_time",
        "last_ingested_at",
        "ohlc_violations",
        "null_ohlc_values",
        "outside_session",
        "duplicate_versions",
        "expected_series",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (date(2026, 8, 12), 375, 1, NOW, NOW, NOW, 0, 0, 0, 20, 1),
            (date(2026, 8, 10), 375, 1, NOW, NOW, NOW, 0, 0, 0, 20, 1),
        )
    )

    page = IndiaHubRepository(client).list_instrument_days(
        INSTRUMENT_ID,
        date_from=date(2026, 8, 10),
        date_to=date(2026, 8, 12),
        now=NOW,
    )

    days = {item.trading_date: item for item in page.items}
    assert days[date(2026, 8, 12)].check_status == "healthy"
    assert days[date(2026, 8, 11)].check_status == "missing"
    assert days[date(2026, 8, 11)].expected_data_points == 375
    assert days[date(2026, 8, 10)].coverage_percent == 100


class FakeHubIndiaRepository:
    def __init__(self):
        self.kwargs = None
        self.instrument_kwargs = None
        self.day_kwargs = None

    def list_instruments(self, **kwargs):
        self.kwargs = kwargs
        self.instrument_kwargs = kwargs
        return {
            "trading_date": kwargs["trading_date"],
            "market_status": "closed",
            "expected_points_per_series": 375,
            "scope": kwargs["scope"],
            "items": [],
            "total": 0,
            "reference_total": 0,
            "collecting_total": 0,
            "historical_total": 0,
            "not_configured_total": 0,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "data_as_of": None,
        }

    def list_instrument_days(self, instrument_id, **kwargs):
        self.kwargs = {"instrument_id": instrument_id, **kwargs}
        self.day_kwargs = self.kwargs
        return {
            "instrument_id": instrument_id,
            "date_from": kwargs["date_from"],
            "date_to": kwargs["date_to"],
            "items": [],
        }

    def get_instrument(self, instrument_id, **kwargs):
        self.kwargs = {"instrument_id": instrument_id, **kwargs}
        return {
            "instrument_id": instrument_id,
            "symbol": "TCS",
            "name": "Tata Consultancy Services",
            "exchange_code": "NSE",
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "status": "active",
            "source": "upstox",
            "collection_status": "collecting",
            "data_points": 7500,
            "trading_days": 20,
            "unique_series": 1,
            "first_bar_time": NOW,
            "last_bar_time": NOW,
            "last_ingested_at": NOW,
            "expected_series": 1,
            "selected_data_points": 375,
            "selected_unique_series": 1,
            "expected_data_points": 375,
            "coverage_percent": 100,
            "ohlc_violations": 0,
            "null_ohlc_values": 0,
            "outside_session": 0,
            "duplicate_versions": 0,
            "quality_issue_count": 0,
            "check_status": "healthy",
            "check_reason": "Coverage and value checks passed.",
        }


def test_hub_india_routes_are_private_boundary_endpoints_without_bearer_key():
    repository = FakeHubIndiaRepository()
    app.dependency_overrides[get_india_hub_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            instruments = client.get(
                "/hub/api/v1/india/instruments",
                params={
                    "trading_date": "2026-08-12",
                    "scope": "collecting",
                    "search": "TCS",
                    "limit": 25,
                },
            )
            instrument = client.get(
                f"/hub/api/v1/india/instruments/{INSTRUMENT_ID}",
                params={"trading_date": "2026-08-12"},
            )
            days = client.get(
                f"/hub/api/v1/india/instruments/{INSTRUMENT_ID}/days",
                params={"date_from": "2026-08-01", "date_to": "2026-08-12"},
            )
    finally:
        app.dependency_overrides.clear()

    assert instruments.status_code == 200
    assert instrument.status_code == 200
    assert instrument.json()["symbol"] == "TCS"
    assert repository.instrument_kwargs["scope"] == "collecting"
    assert repository.day_kwargs["instrument_id"] == INSTRUMENT_ID
    assert days.status_code == 200


def test_hub_instrument_history_rejects_ranges_over_120_days():
    repository = FakeHubIndiaRepository()
    app.dependency_overrides[get_india_hub_repository] = lambda: repository
    try:
        response = TestClient(app).get(
            f"/hub/api/v1/india/instruments/{INSTRUMENT_ID}/days",
            params={"date_from": "2026-01-01", "date_to": "2026-08-12"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert repository.kwargs is None
