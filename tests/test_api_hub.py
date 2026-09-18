from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from factorlab.api.app import app, get_hub_overview_service
from factorlab.api.hub import HubOverviewService, HubRepository


class QueryResult:
    def __init__(self, columns, rows):
        self.column_names = columns
        self.result_rows = rows


def result(columns, *rows):
    return QueryResult(columns, list(rows))


class HubQueryClient:
    def __init__(self, now):
        self.now = now
        self.calls = []

    def query(self, query, parameters=None):
        self.calls.append((query, parameters))
        if "FROM system.tables AS tables" in query:
            return result(
                ["name", "engine", "stored_rows", "bytes_on_disk"],
                ("market_candles_1min", "ReplacingMergeTree", 10_000, 500_000),
                ("market_candles_daily", "ReplacingMergeTree", 0, 0),
                ("alt_political_trades", "ReplacingMergeTree", 244, 80_000),
                ("ingestion_runs", "ReplacingMergeTree", 300, 15_000),
                ("future_table", "MergeTree", 4, 100),
            )
        if "FROM india_expected_series FINAL WHERE active" in query:
            return result(["expected_series"], (1,))
        if "failed_pipelines" in query:
            return result(["failed_pipelines"], (0,))
        if "FROM market_candles_1min" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (
                    datetime(2026, 8, 1, tzinfo=UTC),
                    self.now,
                    self.now - timedelta(minutes=5),
                    101,
                ),
            )
        if "FROM market_candles_daily" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (None, None, None, 0),
            )
        if "FROM alt_political_trades" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (date(2025, 1, 1), date(2026, 8, 18), self.now - timedelta(hours=12), 0),
            )
        if "FROM ingestion_runs" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (self.now - timedelta(days=30), self.now, self.now, 12),
            )
        raise AssertionError(query)


def test_overview_includes_empty_unknown_and_schedule_aware_tables():
    now = datetime(2026, 8, 20, 5, 30, tzinfo=UTC)  # 11:00 in India
    overview = HubRepository(HubQueryClient(now)).get_overview(now=now)
    tables = {item.name: item for item in overview.tables}

    assert overview.summary.table_count == 5
    assert overview.summary.populated_tables == 4
    assert overview.summary.stored_rows == 10_548
    assert tables["market_candles_1min"].today_status == "not_expected"
    assert overview.india.status == "healthy"
    assert tables["market_candles_1min"].today_rows == 101
    assert tables["market_candles_daily"].today_status == "not_expected"
    assert tables["alt_political_trades"].today_status == "healthy"
    assert tables["future_table"].today_status == "not_configured"
    assert overview.india.expected_data_points == 106
    assert overview.india.coverage_percent == 95.28


def test_overview_marks_stale_political_and_failed_ingestion_attention():
    now = datetime(2026, 8, 20, 5, 30, tzinfo=UTC)
    client = HubQueryClient(now)
    original_query = client.query

    def query(query, parameters=None):
        if "failed_pipelines" in query:
            return result(["failed_pipelines"], (2,))
        if "FROM alt_political_trades" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (date(2025, 1, 1), date(2026, 8, 1), now - timedelta(days=3), 0),
            )
        return original_query(query, parameters)

    client.query = query
    overview = HubRepository(client).get_overview(now=now)
    tables = {item.name: item for item in overview.tables}

    assert tables["alt_political_trades"].today_status == "attention"
    assert tables["ingestion_runs"].today_status == "attention"
    assert overview.political.status == "attention"
    assert overview.summary.attention_tables == 2


def test_overview_service_caches_until_ttl_expires():
    class Repository:
        def __init__(self):
            self.calls = 0

        def get_overview(self):
            self.calls += 1
            return HubRepository(HubQueryClient(datetime(2026, 8, 20, 5, 30, tzinfo=UTC))).get_overview(
                now=datetime(2026, 8, 20, 5, 30, tzinfo=UTC)
            )

    ticks = iter([0.0, 0.0, 10.0, 60.0, 60.0])
    repository = Repository()
    service = HubOverviewService(repository, ttl_seconds=55, clock=lambda: next(ticks))

    first = service.get_overview()
    second = service.get_overview()
    third = service.get_overview()

    assert first is second
    assert third is not second
    assert repository.calls == 2


def test_hub_endpoint_uses_private_overview_service():
    now = datetime(2026, 8, 20, 5, 30, tzinfo=UTC)
    service = HubOverviewService(HubRepository(HubQueryClient(now)), ttl_seconds=55)
    app.dependency_overrides[get_hub_overview_service] = lambda: service
    try:
        response = TestClient(app).get("/hub/api/v1/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["summary"]["table_count"] == 5
    assert response.json()["tables"][0]["count_kind"] == "stored"
