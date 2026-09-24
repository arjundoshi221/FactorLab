from datetime import UTC, date, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from factorlab.api.app import app, get_political_observability_repository
from factorlab.api.political_observability import PoliticalObservabilityRepository

TODAY = date(2026, 8, 12)
NOW = datetime(2026, 8, 12, 10, tzinfo=UTC)
RUN_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


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


def test_political_repository_dependency_is_request_local(monkeypatch):
    created = []

    def create_repository(*_args):
        repository = object()
        created.append(repository)
        return repository

    monkeypatch.setattr(PoliticalObservabilityRepository, "from_environment", create_repository)

    assert get_political_observability_repository() is not get_political_observability_repository()
    assert len(created) == 2


def test_dashboard_reports_parse_resolution_and_quality_counts():
    columns = [
        "current_legislators",
        "current_committees",
        "filings",
        "parsed_filings",
        "trades",
        "unique_legislators",
        "unique_tickers",
        "unparsed_filings",
        "legislator_match_rate",
        "ticker_resolution_rate",
        "late_disclosures",
        "invalid_trades",
        "latest_filing_date",
        "latest_transaction_date",
        "last_ingested_at",
        "collected_on_date",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (535, 220, 100, 90, 450, 40, 120, 10, 95.555, 80.111, 3, 2, TODAY, TODAY, NOW, 25),
        )
    )

    dashboard = PoliticalObservabilityRepository(client).get_dashboard(as_of_date=TODAY)

    assert dashboard.filings == 100
    assert dashboard.parsed_filings == 90
    assert dashboard.legislator_match_rate == 95.56
    assert dashboard.ticker_resolution_rate == 80.11
    assert dashboard.anomaly_count == 15


def test_coverage_returns_chamber_parse_and_match_rates():
    columns = [
        "chamber",
        "filings",
        "parsed_filings",
        "unparsed_filings",
        "filing_parse_rate",
        "trades",
        "unique_legislators",
        "matched_legislator_trades",
        "legislator_match_rate",
        "unique_tickers",
        "ticker_resolution_rate",
        "first_filing_date",
        "latest_filing_date",
        "last_ingested_at",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (
                "house",
                100,
                90,
                10,
                90.0,
                450,
                40,
                430,
                95.555,
                120,
                80.111,
                date(2026, 1, 1),
                TODAY,
                NOW,
            ),
        )
    )

    page = PoliticalObservabilityRepository(client).list_coverage(year=2026)

    assert page.year == 2026
    assert page.items[0].unparsed_filings == 10
    assert page.items[0].legislator_match_rate == 95.56
    assert client.calls[0][1]["year"] == 2026


def test_collection_activity_separates_physical_filing_and_trade_rows():
    summary_columns = [
        "filing_rows",
        "trade_rows",
        "unique_filings",
        "unique_trades",
        "first_ingested_at",
        "last_ingested_at",
    ]
    bucket_columns = ["bucket", "filing_rows", "trade_rows"]
    client = QueueQueryClient(
        result(summary_columns, (100, 450, 90, 440, NOW, NOW)),
        result(bucket_columns, ("10:00", 100, 450)),
        result(bucket_columns, ("house_clerk_ptr", 0, 450)),
    )

    activity = PoliticalObservabilityRepository(client).get_collection_activity(
        ingestion_date=TODAY
    )

    assert activity.summary.filing_rows == 100
    assert activity.summary.unique_trades == 440
    assert activity.by_hour[0].trade_rows == 450
    assert activity.by_source[0].bucket == "house_clerk_ptr"


def test_freshness_classifies_never_seen_datasets():
    columns = ["dataset", "source", "rows", "last_ingested_at"]
    client = QueueQueryClient(
        result(
            columns,
            ("filings", "house_clerk_filing_index", 100, None),
        )
    )

    page = PoliticalObservabilityRepository(client).list_freshness(stale_after_seconds=172800)

    assert page.items[0].status == "never_seen"
    assert page.items[0].freshness_seconds is None


def test_anomalies_receive_stable_ids_and_forward_filters():
    columns = [
        "anomaly_type",
        "severity",
        "record_type",
        "record_key",
        "legislator_name",
        "ticker",
        "event_date",
        "observed_value",
        "expected_value",
        "explanation",
    ]
    client = QueueQueryClient(
        result(
            columns,
            (
                "late_disclosure",
                "warning",
                "trade",
                "a" * 64,
                "Jane Doe",
                "AAPL",
                TODAY,
                "60",
                "<=45 days",
                "Late disclosure",
            ),
        )
    )

    page = PoliticalObservabilityRepository(client).list_anomalies(
        date_from=date(2026, 1, 1),
        date_to=TODAY,
        severity="warning",
    )

    assert len(page.items[0].anomaly_id) == 24
    assert page.items[0].ticker == "AAPL"
    assert client.calls[0][1]["severity"] == "warning"


def test_metrics_use_allowlisted_table_date_and_expression():
    client = QueueQueryClient(result(["bucket", "value"], (TODAY, 450.0)))

    series = PoliticalObservabilityRepository(client).get_metrics(
        metric="trades",
        date_basis="transaction",
        group_by="day",
        date_from=date(2026, 1, 1),
        date_to=TODAY,
    )

    query = client.calls[0][0]
    assert "FROM (" in query and "alt.political_trades FINAL" in query
    assert "transaction_date AS bucket" in query
    assert series.points[0].value == 450.0


def test_legislator_and_ticker_discovery_queries():
    legislator_columns = [
        "legislator_entity_id",
        "bioguide_id",
        "official_full",
        "chamber",
        "state",
        "district",
        "party",
        "term_start",
        "term_end",
        "in_office",
        "source",
        "ingested_at",
    ]
    ticker_columns = [
        "ticker",
        "trades",
        "filings",
        "legislators",
        "purchases",
        "sales",
        "amount_min_total",
        "amount_max_total",
        "first_transaction_date",
        "latest_transaction_date",
        "last_ingested_at",
    ]
    client = QueueQueryClient(
        result(
            legislator_columns,
            (
                UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "D000001",
                "Jane Doe",
                "rep",
                "CA",
                1,
                "Democrat",
                date(2025, 1, 3),
                date(2027, 1, 3),
                True,
                "congress_legislators",
                NOW,
            ),
        ),
        result(
            ticker_columns,
            ("AAPL", 10, 3, 2, 6, 4, 10000, 150000, date(2026, 1, 1), TODAY, NOW),
        ),
    )
    repository = PoliticalObservabilityRepository(client)

    legislators = repository.list_legislators(search="jane", state_code="CA")
    tickers = repository.list_tickers(search="AA")

    assert legislators.items[0].bioguide_id == "D000001"
    assert tickers.items[0].ticker == "AAPL"
    assert client.calls[0][1]["state"] == "CA"


def test_legislator_and_ticker_summaries_combine_drill_down_data():
    legislator_columns = [
        "legislator_entity_id",
        "bioguide_id",
        "official_full",
        "chamber",
        "state",
        "district",
        "party",
        "term_start",
        "term_end",
        "in_office",
        "source",
        "ingested_at",
    ]
    ticker_columns = [
        "ticker",
        "trades",
        "filings",
        "legislators",
        "purchases",
        "sales",
        "amount_min_total",
        "amount_max_total",
        "first_transaction_date",
        "latest_transaction_date",
        "last_ingested_at",
    ]
    client = QueueQueryClient(
        result(
            legislator_columns,
            (
                UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "D000001",
                "Jane Doe",
                "rep",
                "CA",
                1,
                "Democrat",
                date(2025, 1, 3),
                date(2027, 1, 3),
                True,
                "congress_legislators",
                NOW,
            ),
        ),
        result(
            [
                "filings",
                "trades",
                "unique_tickers",
                "purchases",
                "sales",
                "amount_min_total",
                "amount_max_total",
                "first_transaction_date",
                "latest_transaction_date",
                "last_ingested_at",
            ],
            (3, 10, 2, 6, 4, 10000, 150000, date(2026, 1, 1), TODAY, NOW),
        ),
        result(
            ticker_columns,
            ("AAPL", 10, 3, 2, 6, 4, 10000, 150000, date(2026, 1, 1), TODAY, NOW),
        ),
        result(
            ["legislator_name", "bioguide_id", "trades", "amount_min_total"],
            ("Jane Doe", "D000001", 6, 6000),
        ),
        result(["transaction_type", "trades"], ("purchase", 6), ("sale_full", 4)),
    )
    repository = PoliticalObservabilityRepository(client)

    legislator = repository.get_legislator_summary("D000001")
    ticker = repository.get_ticker_summary("AAPL")

    assert legislator.filings == 3
    assert legislator.unique_tickers == 2
    assert ticker.ticker.ticker == "AAPL"
    assert ticker.top_legislators[0]["legislator_name"] == "Jane Doe"
    assert len(ticker.transaction_types) == 2


def test_political_run_queries_are_isolated_by_market_code():
    columns = [
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
    client = QueueQueryClient(
        result(
            columns,
            (
                RUN_ID,
                "political_bootstrap",
                "political",
                "house",
                "success",
                NOW,
                NOW,
                24,
                24,
                0,
                500,
                None,
                "{}",
            ),
        ),
        result(
            [
                "source",
                "pipeline",
                "run_status",
                "last_run_started_at",
                "last_run_completed_at",
                "last_success_at",
                "last_ingested_at",
            ],
            ("political", "political_bootstrap", "success", NOW, NOW, NOW, None),
        ),
    )
    repository = PoliticalObservabilityRepository(client)

    page = repository.list_ingestion_runs(limit=20)
    sources = repository.list_source_status(stale_after_seconds=172800)

    assert page.items[0].pipeline == "political_bootstrap"
    assert "country_code = 'US'" in client.calls[0][0]
    assert sources.items[0].status == "stale"


def test_all_political_observability_routes_require_auth(monkeypatch):
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    paths = [
        "/api/v1/political/dashboard",
        "/api/v1/political/coverage",
        "/api/v1/political/collection/activity",
        "/api/v1/political/freshness",
        "/api/v1/political/anomalies",
        "/api/v1/political/metrics/timeseries",
        "/api/v1/political/legislators",
        "/api/v1/political/legislators/D000001/summary",
        "/api/v1/political/tickers",
        "/api/v1/political/tickers/AAPL/summary",
        "/api/v1/political/ingestion/runs",
        f"/api/v1/political/ingestion/runs/{RUN_ID}",
        "/api/v1/political/sources/status",
    ]
    with TestClient(app) as client:
        responses = [client.get(path) for path in paths]
    assert all(response.status_code == 401 for response in responses)


class FakeDashboardRepository:
    def __init__(self):
        self.as_of_date = None

    def get_dashboard(self, *, as_of_date):
        self.as_of_date = as_of_date
        return {
            "as_of_date": as_of_date,
            "current_legislators": 535,
            "current_committees": 220,
            "filings": 100,
            "parsed_filings": 90,
            "trades": 450,
            "unique_legislators": 40,
            "unique_tickers": 120,
            "unparsed_filings": 10,
            "legislator_match_rate": 95.5,
            "ticker_resolution_rate": 80.0,
            "late_disclosures": 3,
            "anomaly_count": 15,
            "latest_filing_date": None,
            "latest_transaction_date": None,
            "last_ingested_at": None,
            "collected_on_date": 0,
        }


def test_political_dashboard_endpoint_forwards_date(monkeypatch):
    repository = FakeDashboardRepository()
    app.dependency_overrides[get_political_observability_repository] = lambda: repository
    monkeypatch.setenv("FACTORLAB_API_KEY", "test-secret")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/political/dashboard",
                params={"as_of_date": TODAY.isoformat()},
                headers={"Authorization": "Bearer test-secret"},
            )
            hub_response = client.get(
                "/hub/api/v1/political/dashboard",
                params={"as_of_date": TODAY.isoformat()},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert hub_response.status_code == 200
    assert hub_response.json()["trades"] == 450
    assert repository.as_of_date == TODAY
