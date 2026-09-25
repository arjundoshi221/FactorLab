from datetime import UTC, datetime

from fastapi.testclient import TestClient

from factorlab.api.app import app, get_schema_map_service
from factorlab.api.schema_map import V2_DATABASES, SchemaMapRepository, SchemaMapService

TABLE_COLUMNS = [
    "database", "name", "engine", "stored_rows", "bytes_on_disk", "primary_key", "sorting_key", "partition_key",
]
COLUMN_COLUMNS = [
    "database", "table", "name", "type", "position", "default_kind", "default_expression",
    "is_in_primary_key", "is_in_sorting_key", "is_in_partition_key",
]


class QueryResult:
    def __init__(self, columns, rows):
        self.column_names = columns
        self.result_rows = rows


def result(columns, *rows):
    return QueryResult(columns, list(rows))


class V2SchemaQueryClient:
    def __init__(self):
        self.calls = []

    def query(self, query, parameters=None):
        self.calls.append(query)
        if "FROM system.tables AS tables" in query:
            assert parameters == {"databases": list(V2_DATABASES)}
            return result(
                TABLE_COLUMNS,
                ("ref", "listings", "ReplacingMergeTree", 10, 500, "listing_id", "listing_id", ""),
                ("ref", "countries", "ReplacingMergeTree", 2, 50, "country_code", "country_code", ""),
                ("raw", "archive", "MergeTree", 9, 900, "source", "source, fetched_at", ""),
                ("market", "bars", "ReplacingMergeTree", 100, 5000, "", "listing_id, bar_time", "toYYYYMM(bar_time)"),
                ("research", "bars", "View", 0, 0, "", "", ""),
            )
        if "FROM system.columns" in query:
            assert parameters == {"databases": list(V2_DATABASES)}
            return result(
                COLUMN_COLUMNS,
                ("ref", "listings", "listing_id", "UUID", 1, "", "", 1, 1, 0),
                ("ref", "listings", "country_code", "FixedString(2)", 2, "", "", 0, 0, 0),
                ("ref", "countries", "country_code", "FixedString(2)", 1, "", "", 1, 1, 0),
                ("raw", "archive", "raw_id", "UUID", 1, "", "", 0, 1, 0),
                ("market", "bars", "listing_id", "UUID", 1, "", "", 0, 1, 0),
                ("market", "bars", "bar_time", "DateTime64(3, 'UTC')", 2, "", "", 0, 1, 1),
                ("market", "bars", "raw_id", "Nullable(UUID)", 3, "", "", 0, 0, 0),
                ("research", "bars", "bar_time", "DateTime64(3, 'UTC')", 1, "", "", 0, 0, 0),
            )
        raise AssertionError(query)


def test_schema_map_describes_v2_tables_columns_areas_and_link_categories():
    schema = SchemaMapRepository(V2SchemaQueryClient()).get_schema_map(now=datetime(2026, 9, 26, tzinfo=UTC))
    tables = {table.name: table for table in schema.tables}

    bars = tables["market.bars"]
    assert bars.namespace == "market" and bars.title == "Price bars" and bars.kind == "table"
    assert bars.summary.startswith("Open, high, low, close")
    assert any(note.startswith("Filter by resolution") for note in bars.notes)
    assert next(column for column in bars.columns if column.name == "bar_time").description == "When the bar starts, in UTC."
    assert tables["research.bars"].kind == "view"

    assert [area.id for area in schema.areas] == ["ref", "market", "raw", "research"]
    assert schema.areas[1].title == "Market data"

    categories = {(item.source.table, item.source.column): item.category for item in schema.relationships}
    assert categories[("market.bars", "listing_id")] == "identity"
    assert categories[("market.bars", "raw_id")] == "lineage"
    assert categories[("ref.listings", "country_code")] == "lookup"
    optional = next(item for item in schema.relationships if item.source.column == "raw_id")
    assert optional.optional is True
    assert not hasattr(schema, "layout")


def test_schema_service_caches_and_both_paths_serve_the_same_map():
    client = V2SchemaQueryClient()
    now = [0.0]
    service = SchemaMapService(SchemaMapRepository(client), clock=lambda: now[0])
    app.dependency_overrides[get_schema_map_service] = lambda: service
    try:
        http = TestClient(app)
        first = http.get("/hub/api/v1/schema-map")
        alias = http.get("/hub/api/v1/schema-map/v2")
        assert http.put("/hub/api/v1/schema-map/layout", json={}).status_code in {404, 405}
        assert http.put("/hub/api/v1/schema-map/v2/layout", json={}).status_code in {404, 405}
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == alias.status_code == 200
    assert first.json() == alias.json()
    assert len([call for call in client.calls if "system.tables" in call]) == 1
    now[0] = 61.0
    assert service.get_schema_map() is not None  # after the TTL a fresh read happens
    assert len([call for call in client.calls if "system.tables" in call]) == 2


def test_schema_map_reports_503_when_clickhouse_is_down():
    class Broken:
        def get_schema_map(self):
            raise RuntimeError("down")

    app.dependency_overrides[get_schema_map_service] = lambda: Broken()
    try:
        response = TestClient(app).get("/hub/api/v1/schema-map")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503


class EmptyV2SchemaQueryClient:
    def query(self, query, parameters=None):
        if "FROM system.tables AS tables" in query:
            return result(TABLE_COLUMNS)
        raise AssertionError(query)


def test_schema_map_falls_back_to_migration_ddl_preview_when_databases_are_absent():
    schema = SchemaMapRepository(EmptyV2SchemaQueryClient()).get_schema_map(now=datetime(2026, 9, 21, tzinfo=UTC))

    namespaces = {table.name.split(".", 1)[0] for table in schema.tables}
    assert namespaces == set(V2_DATABASES)
    assert len(schema.tables) >= 60
    bars = next(table for table in schema.tables if table.name == "market.bars")
    assert bars.columns and bars.title == "Price bars"
    assert next(table for table in schema.tables if table.name == "research.bars").kind == "view"
    assert schema.warnings[0].startswith("Preview mode:")
    assert len(schema.areas) == len(V2_DATABASES)
