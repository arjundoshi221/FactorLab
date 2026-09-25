import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factorlab.api.app import app, get_catalog_service
from factorlab.api.catalog import CatalogService, pipeline_status
from factorlab.api.catalog_pipelines import PIPELINES, PIPELINES_BY_ID
from factorlab.api.catalog_query import QuerySlots, scrub_text
from factorlab.api.hub import HubTable

NOW = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)  # 10:30 IST on a Friday: NSE open, NYSE closed
LAST_BAR = datetime(2026, 9, 25, 4, 55, tzinfo=UTC)

TABLES = [
    # database, name, engine, rows, bytes, primary, sorting, partition, columns
    ("market", "bars", "ReplacingMergeTree", 10_000_000, 900_000_000, "country_code, listing_id",
     "country_code, listing_id, resolution, bar_time", "toYYYYMM(bar_time)",
     [("country_code", "FixedString(2)"), ("listing_id", "UUID"), ("resolution", "LowCardinality(String)"),
      ("bar_time", "DateTime64(3, 'UTC')"), ("trade_date", "Date"), ("close", "Nullable(Decimal(18, 6))"),
      ("volume", "Nullable(UInt64)"), ("source", "LowCardinality(String)"), ("ingested_at", "DateTime64(3, 'UTC')")]),
    ("raw", "archive", "MergeTree", 5_000_000, 50_000_000_000, "source", "source, fetched_at", "toYYYYMM(fetched_at)",
     [("raw_id", "UUID"), ("source", "LowCardinality(String)"), ("source_url", "String"), ("request_key", "String"),
      ("status_code", "Nullable(UInt16)"), ("response_headers", "String"), ("response_body", "String"),
      ("metadata_json", "String"), ("fetched_at", "DateTime64(3, 'UTC')")]),
    ("ref", "listings", "ReplacingMergeTree", 3_200, 90_000, "listing_id", "listing_id", "",
     [("listing_id", "UUID"), ("trading_symbol", "LowCardinality(String)"), ("active", "Bool"),
      ("ingested_at", "DateTime64(3, 'UTC')")]),
    ("meta", "ingestion_runs", "ReplacingMergeTree", 900, 40_000, "pipeline", "pipeline, started_at", "",
     [("run_id", "UUID"), ("pipeline", "LowCardinality(String)"), ("status", "LowCardinality(String)"),
      ("started_at", "DateTime64(3, 'UTC')"), ("error", "Nullable(String)"), ("metadata_json", "String"),
      ("ingested_at", "DateTime64(3, 'UTC')")]),
    ("research", "bars", "View", 0, 0, "", "", "", [("bar_time", "DateTime64(3, 'UTC')")]),
]


class Result:
    def __init__(self, columns, rows):
        self.column_names = columns
        self.result_rows = rows


class Stream:
    def __init__(self, blocks):
        self.blocks = blocks

    def __enter__(self):
        return iter(self.blocks)

    def __exit__(self, *args):
        return False


class FakeClient:
    """Answers schema metadata and records every data query with its parameters and settings."""

    def __init__(self):
        self.calls = []
        self.rows = {}
        self.blocks = []
        self.error = None

    def query(self, query, parameters=None, settings=None):
        self.calls.append((query, parameters or {}, settings or {}))
        if "FROM system.tables AS tables" in query:
            return Result(
                ["database", "name", "engine", "stored_rows", "bytes_on_disk", "primary_key", "sorting_key",
                 "partition_key"],
                [row[:8] for row in TABLES],
            )
        if "FROM system.columns" in query:
            return Result(
                ["database", "table", "name", "type", "position", "default_kind", "default_expression",
                 "is_in_primary_key", "is_in_sorting_key", "is_in_partition_key"],
                [(db, name, column, kind, position, "", "", 0, 0, 0)
                 for db, name, *_, columns in TABLES for position, (column, kind) in enumerate(columns, 1)],
            )
        if "meta.hub_schema_layouts" in query:
            return Result(["revision", "schema_fingerprint", "layout_json", "updated_at"], [])
        if self.error is not None:
            raise self.error
        for marker, result in self.rows.items():
            if marker in query:
                return result
        return Result([], [])

    def query_row_block_stream(self, query, parameters=None, settings=None):
        self.calls.append((query, parameters or {}, settings or {}))
        return Stream(self.blocks)

    def data_calls(self):
        return [call for call in self.calls if "system." not in call[0] and "hub_schema_layouts" not in call[0]]


class Overview:
    def get_overview(self):
        def table(name, rows, last, status="not_expected"):
            return HubTable(name=name, domain="d", category="c", engine="e", stored_rows=rows, bytes_on_disk=1,
                            last_data_at=last, today_status=status, status_reason="reason")

        return type("O", (), {"tables": [
            table("market.bars", 10_000_000, LAST_BAR.isoformat()),
            table("raw.archive", 5_000_000, LAST_BAR.isoformat()),
            table("ref.listings", 3_200, "2026-09-24T00:00:00+00:00"),
            table("meta.ingestion_runs", 900, LAST_BAR.isoformat(), "healthy"),
        ]})()


@pytest.fixture
def client_and_service(monkeypatch):
    for name in ("FACTORLAB_CATALOG_PREVIEW", "FACTORLAB_CATALOG_CSV", "FACTORLAB_CATALOG_PREVIEW_DENY",
                 "FACTORLAB_CATALOG_CSV_MAX_ROWS"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeClient()
    service = CatalogService(fake, Overview(), now=lambda: NOW)
    app.dependency_overrides[get_catalog_service] = lambda: service
    try:
        yield fake, service, TestClient(app)
    finally:
        app.dependency_overrides.clear()


def last_data_call(fake):
    return fake.data_calls()[-1]


def test_index_describes_namespaces_tables_and_views(client_and_service):
    _, _, http = client_and_service
    body = http.get("/hub/api/v1/catalog").json()
    namespaces = {item["id"]: item for item in body["namespaces"]}
    tables = {item["name"]: item for item in body["tables"]}
    assert namespaces["market"]["title"] == "Market data"
    assert namespaces["research"]["view_count"] == 1
    assert tables["market.bars"]["title"] == "Price bars"
    assert tables["market.bars"]["column_notes"]["bar_time"].startswith("When the bar starts")
    assert tables["research.bars"]["kind"] == "view"
    assert tables["research.bars"]["previewable"] is False
    assert body["previews_enabled"] is True


def test_detail_explains_columns_keys_relations_writers_and_window(client_and_service):
    _, _, http = client_and_service
    detail = http.get("/hub/api/v1/catalog/tables/market.bars").json()
    columns = {item["name"]: item for item in detail["column_details"]}
    assert columns["close"]["friendly_type"] == "Decimal number"
    assert "gte" in columns["close"]["operators"]
    assert columns["bar_time"]["operators"] == ["gte", "lt", "notnull", "null"]
    assert any(item["table"] == "ref.listings" and item["column"] == "listing_id" for item in detail["related"])
    assert "india_intraday_1min" in {item["id"] for item in detail["writers"]}
    assert "newer version replaces" in detail["keys"]["explanation"][0]
    preview = detail["preview"]
    assert preview["windowed"] is True and preview["time_column"] == "bar_time"
    assert preview["default_end"].startswith("2026-09-25T04:55:01")
    assert preview["default_start"].startswith("2026-09-24T04:55:01")
    raw = http.get("/hub/api/v1/catalog/tables/raw.archive").json()
    assert set(raw["preview"]["hidden_columns"]) == {"response_body", "response_headers", "metadata_json"}


def test_rows_query_is_windowed_bounded_read_only_and_parameterized(client_and_service):
    fake, _, http = client_and_service
    fake.rows["FROM market.bars"] = Result(
        ["country_code", "listing_id", "resolution", "bar_time", "trade_date", "close", "volume", "source",
         "ingested_at"],
        [("IN", None, "1min", LAST_BAR, date(2026, 9, 25), 101.5, 10, "upstox", LAST_BAR)],
    )
    injection = "x' OR 1=1 --"
    response = http.get(
        "/hub/api/v1/catalog/tables/market.bars/rows",
        params={"f.source": f"eq:{injection}", "f.close": "gte:100", "limit": 25},
    )
    assert response.status_code == 200, response.text
    sql, parameters, settings = last_data_call(fake)
    assert injection not in sql and injection in parameters.values()
    assert "FROM market.bars FINAL" in sql
    assert "`bar_time` >= parseDateTime64BestEffort(" in sql and "`bar_time` < parseDateTime64BestEffort(" in sql
    assert "toFloat64(`close`) >= {p3:Float64}" in sql
    assert sql.endswith("LIMIT 26 OFFSET 0")
    assert settings["readonly"] == 2
    assert settings["max_execution_time"] == 15
    assert settings["max_rows_to_read"] == 50_000_000
    assert settings["log_comment"] == "hub-catalog:rows"
    body = response.json()
    assert body["rows"][0][3] == LAST_BAR.isoformat()
    assert body["latest_version_only"] is True


def test_rejects_unknown_tables_columns_operators_and_oversized_requests(client_and_service):
    fake, _, http = client_and_service
    base = "/hub/api/v1/catalog/tables"
    assert http.get(f"{base}/market.bars;DROP TABLE x/rows").status_code in {404, 422}
    assert http.get(f"{base}/market.nothing").status_code == 404
    assert http.get(f"{base}/market.bars/rows", params={"f.nope": "eq:1"}).status_code == 400
    assert http.get(f"{base}/market.bars/rows", params={"f.bar_time": "contains:1"}).status_code == 400
    assert http.get(f"{base}/market.bars/rows", params={"f.volume": "gt:abc"}).status_code == 400
    assert http.get(f"{base}/market.bars/rows", params={"sort": "nope"}).status_code == 400
    assert http.get(f"{base}/market.bars/rows", params={"limit": 1_000}).status_code == 422
    too_wide = {"start": "2026-07-01T00:00:00Z", "end": "2026-09-01T00:00:00Z"}
    assert http.get(f"{base}/market.bars/rows", params=too_wide).status_code == 400
    many = [("f.source", "eq:x")] * 9
    assert http.get(f"{base}/market.bars/rows", params=many).status_code == 400
    assert http.get(f"{base}/research.bars/rows").status_code == 403
    assert not any("nope" in call[0] or "DROP" in call[0] for call in fake.calls)


def test_raw_archive_never_selects_payloads_and_strips_query_strings(client_and_service):
    fake, _, http = client_and_service
    fake.rows["FROM raw.archive"] = Result(
        ["raw_id", "source", "source_url", "request_key", "status_code", "fetched_at"],
        [(None, "schwab", "https://api.example.com/v1/bars?token=abc123&x=1", "AAPL?apikey=abc", 200, LAST_BAR)],
    )
    response = http.get("/hub/api/v1/catalog/tables/raw.archive/rows")
    assert response.status_code == 200, response.text
    sql, _, _ = last_data_call(fake)
    for hidden in ("response_body", "response_headers", "metadata_json"):
        assert hidden not in sql
    assert "cutQueryStringAndFragment(`source_url`) AS `source_url`" in sql
    assert "FINAL" not in sql
    row = response.json()["rows"][0]
    assert row[2] == "https://api.example.com/v1/bars"
    assert row[3] == "AAPL"
    base = "/hub/api/v1/catalog/tables/raw.archive/rows"
    assert http.get(base, params={"f.response_body": "contains:x"}).status_code == 400
    assert http.get(base, params={"sort": "response_headers"}).status_code == 400


def test_scrubber_redacts_credentials_and_truncates():
    text, _ = scrub_text('Bearer abc.def-123 failed for https://x/y?access_token=abc&code=xyz {"token": "abc"}', limit=500)
    assert "abc.def" not in text and "access_token=[redacted]" in text and "code=[redacted]" in text
    assert '"token": "[redacted]"' in text
    assert "country_code=IN" in scrub_text("country_code=IN", limit=50)[0]
    clipped, truncated = scrub_text("x" * 600, limit=500)
    assert truncated and len(clipped) == 500


def test_hidden_run_metadata_and_scrubbed_errors(client_and_service):
    fake, _, http = client_and_service
    fake.rows["FROM meta.ingestion_runs"] = Result(
        ["run_id", "pipeline", "status", "started_at", "error", "ingested_at"],
        [("r1", "us_live", "failed", LAST_BAR, "HTTP 401 with Bearer abc.def", LAST_BAR)],
    )
    response = http.get("/hub/api/v1/catalog/tables/meta.ingestion_runs/rows")
    sql, _, _ = last_data_call(fake)
    assert "metadata_json" not in sql
    assert "WHERE" not in sql  # small tables are previewed whole
    assert response.json()["rows"][0][4] == "HTTP 401 with Bearer [redacted]"
    http.get("/hub/api/v1/catalog/tables/meta.ingestion_runs/rows", params={"start": "2026-09-25T00:00:00Z"})
    sql, parameters, _ = last_data_call(fake)
    assert "`started_at` >= parseDateTime64BestEffort(" in sql  # an explicit range still narrows small tables
    assert "2026-09-25 00:00:00.000" in parameters.values()


def test_switches_and_deny_list_disable_previews(client_and_service, monkeypatch):
    _, _, http = client_and_service
    monkeypatch.setenv("FACTORLAB_CATALOG_PREVIEW_DENY", "market.*")
    assert http.get("/hub/api/v1/catalog/tables/market.bars/rows").status_code == 403
    assert http.get("/hub/api/v1/catalog/tables/ref.listings/rows").status_code == 200
    monkeypatch.delenv("FACTORLAB_CATALOG_PREVIEW_DENY")
    monkeypatch.setenv("FACTORLAB_CATALOG_CSV", "off")
    assert http.get("/hub/api/v1/catalog/tables/ref.listings/rows.csv").status_code == 403
    monkeypatch.setenv("FACTORLAB_CATALOG_PREVIEW", "off")
    assert http.get("/hub/api/v1/catalog/tables/ref.listings/rows").status_code == 403
    assert http.get("/hub/api/v1/catalog/tables/ref.listings/stats").status_code == 403
    detail = http.get("/hub/api/v1/catalog/tables/ref.listings").json()
    assert detail["preview"]["enabled"] is False and "disabled" in detail["preview"]["reason"]


def test_csv_streams_capped_formula_safe_rows(client_and_service, monkeypatch):
    fake, _, http = client_and_service
    monkeypatch.setenv("FACTORLAB_CATALOG_CSV_MAX_ROWS", "2")
    fake.blocks = [[("id1", "=HYPERLINK(1)", True, LAST_BAR), ("id2", "TCS", False, LAST_BAR)],
                   [("id3", "INFY", True, LAST_BAR)]]
    response = http.get("/hub/api/v1/catalog/tables/ref.listings/rows.csv", params={"f.active": "eq:true"})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="ref.listings_2026-09-25.csv"'
    assert response.headers["x-factorlab-row-cap"] == "2"
    lines = response.text.strip().splitlines()
    assert lines[0] == "listing_id,trading_symbol,active,ingested_at"
    assert len(lines) == 3 and "'=HYPERLINK(1)" in lines[1] and "INFY" not in response.text
    sql, _, settings = last_data_call(fake)
    assert sql.endswith("LIMIT 2 OFFSET 0") and settings["log_comment"] == "hub-catalog:csv"


def test_busy_expensive_and_failed_queries_map_to_safe_errors(client_and_service):
    fake, service, http = client_and_service
    service.slots = QuerySlots(1)
    service.slots.acquire()
    assert http.get("/hub/api/v1/catalog/tables/ref.listings/rows").status_code == 429
    service.slots.release()
    fake.error = RuntimeError("Code: 158. DB::Exception: Limit for rows to read exceeded")
    response = http.get("/hub/api/v1/catalog/tables/market.bars/rows", params={"f.source": "eq:a"})
    assert response.status_code == 422 and "Narrow the date range" in response.json()["detail"]
    fake.error = RuntimeError("Code: 62. Syntax error near secret internals")
    response = http.get("/hub/api/v1/catalog/tables/market.bars/rows", params={"f.source": "eq:b"})
    assert response.status_code == 502 and "internals" not in response.text


def test_stats_sample_recent_rows_without_payload_columns(client_and_service):
    fake, _, http = client_and_service
    fake.rows["FROM (SELECT"] = Result(
        ["__rows", "raw_id__nulls", "raw_id__distinct", "source__nulls", "source__distinct", "source__top",
         "status_code__nulls", "status_code__distinct", "status_code__min", "status_code__max"],
        [(100, 0, 100, 0, 2, ["schwab", "upstox"], 5, 3, "200", "503")],
    )
    body = http.get("/hub/api/v1/catalog/tables/raw.archive/stats").json()
    sql, _, settings = last_data_call(fake)
    assert "response_body" not in sql and "LIMIT 100000" in sql and settings["log_comment"] == "hub-catalog:stats"
    columns = {item["name"]: item for item in body["columns"]}
    assert columns["status_code"]["nulls_percent"] == 5.0
    assert columns["source"]["top_values"] == ["schwab", "upstox"]


def test_activity_counts_buckets_and_recent_writer_runs(client_and_service):
    fake, _, http = client_and_service
    fake.rows["GROUP BY bucket"] = Result(["bucket", "rows"], [(date(2026, 9, 24), 500), (date(2026, 9, 25), 120)])
    fake.rows["LIMIT 10"] = Result(
        ["run_id", "pipeline", "source", "status", "started_at", "completed_at", "rows_written", "requested_series",
         "successful_series", "failed_series", "error"],
        [("r1", "india_intraday_1min", "upstox", "success", LAST_BAR, LAST_BAR, 1000, 10, 10, 0, None)],
    )
    body = http.get("/hub/api/v1/catalog/tables/market.bars/activity").json()
    assert [item["rows"] for item in body["buckets"]] == [500, 120]
    assert body["runs"][0]["pipeline"] == "india_intraday_1min"
    activity_sql = next(call for call in fake.data_calls() if "GROUP BY bucket" in call[0])
    assert activity_sql[2]["max_rows_to_read"] == 200_000_000


def test_pipelines_summarize_runs_and_sources(client_and_service):
    fake, _, http = client_and_service
    fake.rows["argMax(status, started_at)"] = Result(
        ["pipeline", "last_status", "last_started_at", "last_completed_at", "runs_24h", "success_24h", "problem_24h",
         "runs_7d", "success_7d", "problem_7d", "rows_24h"],
        [("india_intraday_1min", "success", NOW - timedelta(minutes=4), NOW, 60, 60, 0, 300, 298, 2, 800_000),
         ("political_bootstrap", "failed", NOW - timedelta(hours=3), NOW, 1, 0, 1, 7, 6, 1, 0)],
    )
    fake.rows["toDate(started_at) AS day"] = Result(
        ["pipeline", "day", "success", "problem", "total"], [("india_intraday_1min", date(2026, 9, 25), 60, 0, 60)]
    )
    fake.rows["LIMIT 5 BY pipeline"] = Result(
        ["run_id", "pipeline", "source", "status", "started_at", "completed_at", "rows_written", "requested_series",
         "successful_series", "failed_series", "error"],
        [("r9", "political_bootstrap", "political", "failed", NOW - timedelta(hours=3), None, 0, 1, 0, 1,
          "PDF parse failed; token=abcdef")],
    )
    fake.rows["FROM meta.source_status"] = Result(
        ["country_code", "source", "status", "detail", "checked_at"], [("US", "schwab", "ready", "503 equities", NOW)]
    )
    body = http.get("/hub/api/v1/catalog/pipelines").json()
    cards = {item["id"]: item for item in body["pipelines"]}
    assert set(cards) == set(PIPELINES_BY_ID)
    assert cards["india_intraday_1min"]["status"] == "healthy"
    assert cards["india_intraday_1min"]["days"][0]["success"] == 60
    assert cards["political_bootstrap"]["status"] == "attention"
    assert "token=[redacted]" in cards["political_bootstrap"]["status_reason"]
    assert cards["india_reference_premarket"]["status"] == "not_configured"
    assert body["sources"][0]["source"] == "schwab"
    assert all(call[2].get("readonly") == 2 for call in fake.data_calls())


def test_pipeline_status_respects_market_hours():
    india = PIPELINES_BY_ID["india_intraday_1min"]
    us_live = PIPELINES_BY_ID["us_live"]
    stale = NOW - timedelta(hours=2)
    assert pipeline_status(india, {"last_status": "success"}, stale, [], NOW)[0] == "attention"
    saturday = datetime(2026, 9, 26, 5, 0, tzinfo=UTC)
    status, reason = pipeline_status(india, {"last_status": "success"}, saturday - timedelta(hours=20), [], saturday)
    assert status == "not_expected" and "Outside collection hours" in reason
    status, _ = pipeline_status(us_live, {"last_status": "success", "runs_24h": 100, "problem_24h": 50}, NOW, [], NOW)
    assert status == "attention"
    assert pipeline_status(us_live, {}, None, [], NOW)[0] == "not_expected"


def test_pipeline_runs_page_validates_inputs(client_and_service):
    fake, _, http = client_and_service
    assert http.get("/hub/api/v1/catalog/pipelines/unknown_pipeline/runs").status_code == 404
    assert http.get("/hub/api/v1/catalog/pipelines/us_live/runs", params={"status": "weird"}).status_code == 422
    fake.rows["WHERE pipeline = {pipeline:String}"] = Result(
        ["run_id", "pipeline", "source", "status", "started_at", "completed_at", "rows_written", "requested_series",
         "successful_series", "failed_series", "error"],
        [(f"r{index}", "us_live", "schwab", "success", NOW, NOW, 1, 1, 1, 0, None) for index in range(3)],
    )
    body = http.get("/hub/api/v1/catalog/pipelines/us_live/runs", params={"limit": 2}).json()
    assert len(body["items"]) == 2 and body["has_more"] is True
    sql, parameters, _ = last_data_call(fake)
    assert parameters["pipeline"] == "us_live" and "LIMIT 3 OFFSET 0" in sql


def test_registry_matches_code_and_schema():
    root = Path(__file__).resolve().parents[1]
    code = "\n".join(path.read_text(encoding="utf-8") for folder in ("scripts", "src/factorlab")
                     for path in (root / folder).rglob("*.py"))
    sql = "\n".join(path.read_text(encoding="utf-8") for path in (root / "sql/clickhouse/v2").glob("wave_*.sql"))
    defined = set(re.findall(r"CREATE (?:TABLE|VIEW) IF NOT EXISTS ([a-z_]+\.[a-z0-9_]+)", sql))
    for pipeline in PIPELINES:
        prefix = pipeline.id.rsplit("_", 1)[0]
        assert f'"{pipeline.id}"' in code or f'"{prefix}_{{' in code or f'f"{prefix}_{{' in code, pipeline.id
        assert set(pipeline.tables_written) <= defined, pipeline.id


def test_spa_routes_serve_the_hub(monkeypatch, tmp_path):
    response = TestClient(app).get("/data/tables/market.bars")
    assert response.status_code in {200, 503}
    assert TestClient(app).get("/data/tables/bad name").status_code == 422
