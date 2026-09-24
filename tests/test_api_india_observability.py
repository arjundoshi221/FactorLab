from datetime import UTC, date, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from factorlab.api.app import app, get_india_observability_repository
from factorlab.api.india_observability import IndiaObservabilityRepository

INSTRUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
CONTRACT_ID = UUID(int=0)
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


def test_dashboard_distinguishes_expected_actual_and_backfilled_data():
    client = QueueQueryClient(
        result(
            ["reference_instruments", "expected_series"],
            (2464, 10),
        ),
        result(
            ["series_with_data", "data_points", "last_bar_time", "last_ingested_at"],
            (9, 3375, NOW, NOW),
        ),
        result(
            ["collected_on_date", "backfilled_on_date"],
            (3400, 25),
        ),
        result(
            [
                "listing_id",
                "contract_id",
                "symbol",
                "source",
                "data_points",
                "distinct_closes",
                "ohlc_violations",
                "negative_values",
                "outside_session",
                "price_range_ratio",
                "volume_spike_ratio",
                "duplicate_versions",
                "last_ingested_at",
            ]
        ),
    )

    dashboard = IndiaObservabilityRepository(client).get_dashboard(
        trading_date=TRADING_DATE,
        now=NOW,
    )

    assert dashboard.market_status == "closed"
    assert dashboard.expected_data_points == 3750
    assert dashboard.coverage_percent == 90.0
    assert dashboard.backfilled_on_date == 25
    assert dashboard.freshness_seconds == 0
    assert client.calls[1][1]["trading_date"] == TRADING_DATE


def test_coverage_reports_missing_partial_and_complete_series():
    columns = [
        "listing_id",
        "contract_id",
        "symbol",
        "source",
        "universe",
        "data_points",
        "expected_data_points",
        "first_bar_time",
        "last_bar_time",
        "last_ingested_at",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (INSTRUMENT_ID, CONTRACT_ID, "TCS", "upstox", "demo", 0, 375, None, None, None),
            (UUID(int=2), CONTRACT_ID, "INFY", "upstox", "demo", 300, 375, NOW, NOW, NOW),
            (UUID(int=3), CONTRACT_ID, "RELIANCE", "upstox", "demo", 375, 375, NOW, NOW, NOW),
        )
    )

    page = IndiaObservabilityRepository(client).list_coverage(
        trading_date=TRADING_DATE,
        limit=100,
        now=NOW,
    )

    assert [item.status for item in page.items] == ["missing", "partial", "complete"]
    assert page.items[0].missing_points == 375
    assert page.items[1].coverage_percent == 80.0


def test_collection_activity_groups_physical_ingestion_rows():
    summary_columns = [
        "rows_collected",
        "unique_instruments",
        "unique_series",
        "current_market_date_rows",
        "backfilled_rows",
        "earliest_market_date",
        "latest_market_date",
        "first_ingested_at",
        "last_ingested_at",
    ]
    bucket_columns = ["bucket", "data_points", "unique_series"]
    client = QueueQueryClient(
        result(summary_columns, (400, 5, 10, 375, 25, date(2026, 8, 1), TRADING_DATE, NOW, NOW)),
        result(bucket_columns, ("15:00", 400, 10)),
        result(bucket_columns, ("upstox", 400, 10)),
    )

    activity = IndiaObservabilityRepository(client).get_collection_activity(
        ingestion_date=TRADING_DATE
    )

    assert activity.summary.rows_collected == 400
    assert activity.summary.backfilled_rows == 25
    assert activity.by_hour[0].bucket == "15:00"
    assert activity.by_source[0].bucket == "upstox"


def test_freshness_includes_never_seen_and_stale_series():
    columns = [
        "listing_id",
        "contract_id",
        "symbol",
        "source",
        "last_bar_time",
        "last_ingested_at",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (INSTRUMENT_ID, CONTRACT_ID, "TCS", "upstox", None, None),
            (
                UUID(int=2),
                CONTRACT_ID,
                "INFY",
                "upstox",
                NOW,
                datetime(2026, 8, 12, 10, 50, tzinfo=UTC),
            ),
            (UUID(int=3), CONTRACT_ID, "RELIANCE", "upstox", NOW, NOW),
        )
    )

    page = IndiaObservabilityRepository(client).list_freshness(
        stale_after_seconds=300,
        now=NOW,
    )

    assert [item.status for item in page.items] == ["never_seen", "stale", "live"]
    assert page.items[1].freshness_seconds == 600


def test_gaps_return_contiguous_ranges_with_severity():
    columns = [
        "listing_id",
        "contract_id",
        "symbol",
        "source",
        "gap_start",
        "gap_end",
        "missing_points",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (INSTRUMENT_ID, CONTRACT_ID, "TCS", "upstox", NOW, NOW, 4),
            (UUID(int=2), CONTRACT_ID, "INFY", "upstox", NOW, NOW, 20),
        )
    )

    page = IndiaObservabilityRepository(client).list_gaps(
        trading_date=TRADING_DATE,
        now=NOW,
    )

    assert [item.severity for item in page.items] == ["warning", "critical"]
    assert "numbers({expected_points:UInt16})" in client.calls[0][0]


def test_anomalies_expand_series_checks_into_stable_findings():
    columns = [
        "listing_id",
        "contract_id",
        "symbol",
        "source",
        "data_points",
        "distinct_closes",
        "ohlc_violations",
        "negative_values",
        "outside_session",
        "price_range_ratio",
        "volume_spike_ratio",
        "duplicate_versions",
        "last_ingested_at",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (INSTRUMENT_ID, CONTRACT_ID, "TCS", "upstox", 360, 1, 2, 0, 1, 0.7, 25.0, 8000, NOW),
        )
    )

    page = IndiaObservabilityRepository(client).list_anomalies(
        trading_date=TRADING_DATE,
        now=NOW,
    )

    anomaly_types = {item.anomaly_type for item in page.items}
    assert anomaly_types == {
        "missing_candles",
        "ohlc_violation",
        "outside_session",
        "duplicate_version_burst",
        "flatline",
        "extreme_price_move",
        "volume_spike",
    }
    assert all(len(item.anomaly_id) == 24 for item in page.items)


def test_metrics_only_interpolates_allowlisted_expressions():
    client = QueueQueryClient(
        result(
            ["bucket", "value"],
            (datetime(2026, 8, 12, tzinfo=UTC), 3750.0),
        )
    )

    series = IndiaObservabilityRepository(client).get_metrics(
        metric="data_points",
        group_by="day",
        date_from=TRADING_DATE,
        date_to=TRADING_DATE,
        source="upstox",
    )

    query, parameters = client.calls[0]
    assert "toStartOfDay" in query
    assert "count()" in query
    assert parameters["source"] == "upstox"
    assert series.points[0].value == 3750.0


def test_ingestion_run_and_source_status_read_models():
    run_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    run_columns = [
        "run_id",
        "pipeline",
        "source",
        "universe",
        "status",
        "started_at",
        "completed_at",
        "requested_series",
        "successful_series",
        "failed_series",
        "rows_written",
        "error",
        "metadata_json",
    ]
    source_columns = [
        "source",
        "pipeline",
        "run_status",
        "last_run_started_at",
        "last_run_completed_at",
        "last_success_at",
        "last_bar_time",
        "last_ingested_at",
    ]
    client = QueueQueryClient(
        result(
            run_columns,
            (
                run_id,
                "india_intraday_1min",
                "upstox",
                "demo",
                "partial",
                NOW,
                NOW,
                10,
                9,
                1,
                3375,
                "one failed",
                "{}",
            ),
        ),
        result(
            source_columns,
            ("upstox", "india_intraday_1min", "partial", NOW, NOW, NOW, NOW, NOW),
        ),
    )
    repository = IndiaObservabilityRepository(client)

    runs = repository.list_ingestion_runs(status_filter="partial")
    sources = repository.list_source_status()

    assert runs.items[0].failed_series == 1
    assert client.calls[0][1]["status"] == "partial"
    assert sources.items[0].status == "failed"


def test_instrument_summary_combines_reference_contract_and_data_health():
    instrument_columns = [
        "listing_id",
        "security_id",
        "trading_symbol",
        "name",
        "isin",
        "exchange_code",
        "security_type",
        "currency_code",
        "lot_size",
        "tick_size",
        "status",
        "source",
        "first_seen",
        "last_seen",
        "ingested_at",
    ]
    client = QueueQueryClient(
        result(
            instrument_columns,
            (
                INSTRUMENT_ID,
                UUID(int=3),
                "TCS",
                "Tata Consultancy Services",
                "INE467B01029",
                "NSE",
                "common",
                "INR",
                1,
                0.05,
                "active",
                "upstox",
                date(2026, 8, 1),
                TRADING_DATE,
                NOW,
            ),
        ),
        result(
            ["contract_id", "underlying_listing_id", "contract_type", "expiry", "active"],
            (UUID(int=2), INSTRUMENT_ID, "future", date(2026, 8, 27), True),
        ),
        result(
            [
                "data_points",
                "trading_days",
                "unique_series",
                "first_bar_time",
                "last_bar_time",
                "last_ingested_at",
            ],
            (750, 1, 2, NOW, NOW, NOW),
        ),
        result(["expected_series", "today_points"], (2, 740)),
    )

    summary = IndiaObservabilityRepository(client).get_instrument_summary(
        INSTRUMENT_ID,
        now=NOW,
    )

    assert summary.instrument.trading_symbol == "TCS"
    assert summary.contracts[0].contract_type == "future"
    assert summary.data.missing_points_today == 10
    assert summary.data.anomaly_count_today == 1


def test_all_observability_routes_are_authenticated(monkeypatch):
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    paths = [
        "/api/v1/india/dashboard",
        "/api/v1/india/coverage",
        "/api/v1/india/collection/activity",
        "/api/v1/india/freshness",
        "/api/v1/india/gaps",
        "/api/v1/india/anomalies",
        "/api/v1/india/metrics/timeseries",
        f"/api/v1/india/instruments/{INSTRUMENT_ID}/summary",
        "/api/v1/india/ingestion/runs",
        f"/api/v1/india/ingestion/runs/{INSTRUMENT_ID}",
        "/api/v1/india/sources/status",
    ]
    with TestClient(app) as client:
        responses = [client.get(path) for path in paths]
    assert all(response.status_code == 401 for response in responses)


class FakeDashboardRepository:
    def __init__(self):
        self.trading_date = None

    def get_dashboard(self, *, trading_date):
        self.trading_date = trading_date
        return {
            "trading_date": trading_date,
            "market_status": "closed",
            "reference_instruments": 2464,
            "expected_series": 10,
            "series_with_data": 10,
            "data_points": 3750,
            "expected_data_points": 3750,
            "coverage_percent": 100,
            "collected_on_date": 3750,
            "backfilled_on_date": 0,
            "last_bar_time": None,
            "last_ingested_at": None,
            "freshness_seconds": None,
            "anomaly_count": 0,
        }


def test_dashboard_endpoint_forwards_requested_trading_date(monkeypatch):
    repository = FakeDashboardRepository()
    app.dependency_overrides[get_india_observability_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/india/dashboard",
                params={"trading_date": TRADING_DATE.isoformat()},
                headers={"Authorization": "Bearer test-secret"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.trading_date == TRADING_DATE
