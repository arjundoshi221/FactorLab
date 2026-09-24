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
                ["name", "engine", "stored_rows", "bytes_on_disk", "columns"],
                ("market.bars", "ReplacingMergeTree", 10_000, 500_000, ["bar_time", "ingested_at"]),
                ("market.futures_contract_bars", "ReplacingMergeTree", 0, 0, ["bar_time", "ingested_at"]),
                ("alt.political_trades", "ReplacingMergeTree", 244, 80_000, ["transaction_date", "ingested_at"]),
                ("meta.ingestion_runs", "ReplacingMergeTree", 300, 15_000, ["started_at", "ingested_at"]),
                ("future_table", "MergeTree", 4, 100, ["payload"]),
            )
        if "FROM meta.expected_series FINAL" in query:
            return result(["expected_series"], (1,))
        if "failed_pipelines" in query:
            return result(["failed_pipelines"], (0,))
        if "FROM market.bars" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (
                    datetime(2026, 8, 1, tzinfo=UTC),
                    self.now,
                    self.now - timedelta(minutes=5),
                    101,
                ),
            )
        if "FROM market.futures_contract_bars" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (None, None, None, 0),
            )
        if "FROM alt.political_trades" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (date(2025, 1, 1), date(2026, 8, 18), self.now - timedelta(hours=12), 0),
            )
        if "FROM meta.ingestion_runs" in query:
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
    assert tables["market.bars"].today_status == "not_expected"
    assert overview.india.status == "healthy"
    assert tables["market.bars"].today_rows == 101
    assert tables["market.futures_contract_bars"].today_status == "not_expected"
    assert tables["alt.political_trades"].today_status == "healthy"
    assert tables["future_table"].today_status == "not_configured"
    assert overview.india.expected_data_points == 106
    assert overview.india.coverage_percent == 95.28


def test_overview_profiles_every_v2_table_from_its_columns(monkeypatch):
    now = datetime(2026, 9, 25, 5, 30, tzinfo=UTC)
    monkeypatch.setenv("FACTORLAB_RELEASE_ID", "20260925T050000Z-abcdef123456")
    monkeypatch.setenv("FACTORLAB_COMMIT", "a" * 40)
    client = HubQueryClient(now)
    original_query = client.query

    def query(query, parameters=None):
        if "FROM system.tables AS tables" in query:
            return result(
                ["name", "engine", "stored_rows", "bytes_on_disk", "columns"],
                ("market.bars", "ReplacingMergeTree", 10_000, 500_000, ["bar_time", "ingested_at"]),
                ("meta.session_coverage", "ReplacingMergeTree", 50, 900, ["trade_date", "ingested_at"]),
                ("ref.identifier_aliases", "ReplacingMergeTree", 7, 300, ["valid_from", "ingested_at"]),
                ("meta.expected_series_canonical", "ReplacingMergeTree", 9, 400, ["ingested_at"]),
                ("broker.executions", "ReplacingMergeTree", 0, 0, ["exec_time", "ingested_at"]),
                ("research.bars", "View", 0, 0, ["bar_time"]),
            )
        if "FROM meta.session_coverage" in query:
            assert "FINAL" not in query
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (date(1985, 1, 2), date(2026, 9, 24), now - timedelta(hours=1), 5),
            )
        if "FROM ref.identifier_aliases" in query:
            assert "FINAL" not in query
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (date(2020, 1, 1), date(2026, 9, 1), now, 2),
            )
        if "FROM meta.expected_series_canonical" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (now - timedelta(days=2), now - timedelta(days=1), now - timedelta(days=1), 0),
            )
        if "FROM broker.executions" in query or "FROM research.bars" in query:
            raise AssertionError("empty tables and views must not be aggregated")
        return original_query(query, parameters)

    client.query = query
    overview = HubRepository(client).get_overview(now=now)
    tables = {item.name: item for item in overview.tables}

    assert overview.build.release_id == "20260925T050000Z-abcdef123456"
    assert overview.summary.table_count == 5
    assert overview.summary.view_count == 1
    assert tables["meta.session_coverage"].domain == "Data operations"
    assert tables["meta.session_coverage"].first_data_at == "1985-01-02"
    assert tables["ref.identifier_aliases"].category == "Reference"
    assert tables["ref.identifier_aliases"].today_status == "not_expected"
    assert tables["ref.identifier_aliases"].first_data_at == "2020-01-01"
    assert tables["meta.expected_series_canonical"].category == "Pre-cutover archive"
    assert tables["broker.executions"].today_status == "not_configured"
    assert "no producer" in tables["broker.executions"].status_reason
    assert tables["research.bars"].kind == "view"
    assert tables["research.bars"].today_status == "not_expected"


def test_overview_marks_stale_political_and_failed_ingestion_attention():
    now = datetime(2026, 8, 20, 5, 30, tzinfo=UTC)
    client = HubQueryClient(now)
    original_query = client.query

    def query(query, parameters=None):
        if "failed_pipelines" in query:
            return result(["failed_pipelines"], (2,))
        if "FROM alt.political_trades" in query:
            return result(
                ["first_data_at", "last_data_at", "last_ingested_at", "today_rows"],
                (date(2025, 1, 1), date(2026, 8, 1), now - timedelta(days=3), 0),
            )
        return original_query(query, parameters)

    client.query = query
    overview = HubRepository(client).get_overview(now=now)
    tables = {item.name: item for item in overview.tables}

    assert tables["alt.political_trades"].today_status == "attention"
    assert tables["meta.ingestion_runs"].today_status == "attention"
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
