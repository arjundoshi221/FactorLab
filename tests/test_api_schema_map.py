from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from factorlab.api.app import app, get_schema_map_service
from factorlab.api.schema_map import (
    LayoutConflictError,
    LayoutNode,
    LayoutViewport,
    SchemaLayoutUpdate,
    SchemaMapRepository,
    SchemaMapService,
)


class QueryResult:
    def __init__(self, columns, rows):
        self.column_names = columns
        self.result_rows = rows


def result(columns, *rows):
    return QueryResult(columns, list(rows))


class SchemaQueryClient:
    def __init__(self):
        self.saved = None
        self.inserts = []

    def query(self, query, parameters=None):
        if "FROM system.tables AS tables" in query:
            return result(
                [
                    "name",
                    "engine",
                    "stored_rows",
                    "bytes_on_disk",
                    "primary_key",
                    "sorting_key",
                    "partition_key",
                ],
                ("ref_countries", "ReplacingMergeTree", 2, 100, "country_code", "country_code", ""),
                ("ref_exchanges", "ReplacingMergeTree", 3, 200, "exchange_code", "exchange_code", ""),
            )
        if "FROM system.columns" in query:
            return result(
                [
                    "table",
                    "name",
                    "type",
                    "position",
                    "default_kind",
                    "default_expression",
                    "is_in_primary_key",
                    "is_in_sorting_key",
                    "is_in_partition_key",
                ],
                ("ref_countries", "country_code", "FixedString(2)", 1, "", "", 1, 1, 0),
                ("ref_countries", "name", "String", 2, "", "", 0, 0, 0),
                ("ref_exchanges", "exchange_code", "String", 1, "", "", 1, 1, 0),
                ("ref_exchanges", "country_code", "FixedString(2)", 2, "", "", 0, 0, 0),
            )
        if "FROM hub_schema_layouts FINAL" in query:
            if self.saved is None:
                return result(
                    ["revision", "schema_fingerprint", "layout_json", "updated_at"]
                )
            return result(
                ["revision", "schema_fingerprint", "layout_json", "updated_at"],
                self.saved,
            )
        raise AssertionError(query)

    def insert(self, table, data, *, column_names):
        assert table == "hub_schema_layouts"
        self.inserts.append((table, data, column_names))
        row = data[0]
        self.saved = (row[1], row[2], row[3], row[4])


def test_schema_map_returns_live_columns_keys_and_reviewed_relationships():
    schema = SchemaMapRepository(SchemaQueryClient()).get_schema_map(
        now=datetime(2026, 9, 18, tzinfo=UTC)
    )

    assert schema.database == "factorlab"
    assert [table.name for table in schema.tables] == ["ref_countries", "ref_exchanges"]
    assert schema.tables[0].columns[0].in_sorting_key is True
    assert schema.tables[1].stored_rows == 3
    relationship = next(
        item for item in schema.relationships if item.source.table == "ref_exchanges"
    )
    assert relationship.source.column == "country_code"
    assert relationship.target.table == "ref_countries"
    assert relationship.enforced is False
    assert len(schema.schema_fingerprint) == 64
    assert schema.layout.revision == 0


def test_schema_layout_save_is_versioned_and_round_trips():
    client = SchemaQueryClient()
    service = SchemaMapService(SchemaMapRepository(client))
    schema = service.get_schema_map()
    update = SchemaLayoutUpdate(
        base_revision=0,
        schema_fingerprint=schema.schema_fingerprint,
        nodes=[
            LayoutNode(table="ref_countries", x=10, y=20),
            LayoutNode(table="ref_exchanges", x=410, y=20, collapsed=True),
        ],
        viewport=LayoutViewport(x=5, y=8, zoom=0.8),
    )

    saved = service.save_layout(update)

    assert saved.revision == 1
    assert saved.nodes[1].collapsed is True
    assert client.inserts[0][0] == "hub_schema_layouts"
    assert service.get_schema_map().layout.viewport.zoom == 0.8


def test_schema_layout_rejects_stale_revision():
    client = SchemaQueryClient()
    service = SchemaMapService(SchemaMapRepository(client))
    schema = service.get_schema_map()
    client.saved = (
        2,
        schema.schema_fingerprint,
        '{"nodes":[],"viewport":{"x":0,"y":0,"zoom":1}}',
        datetime(2026, 9, 18, tzinfo=UTC),
    )

    with pytest.raises(LayoutConflictError):
        service.save_layout(
            SchemaLayoutUpdate(
                base_revision=1,
                schema_fingerprint=schema.schema_fingerprint,
                nodes=[],
            )
        )


def test_schema_map_endpoints_use_private_service():
    service = SchemaMapService(SchemaMapRepository(SchemaQueryClient()))
    app.dependency_overrides[get_schema_map_service] = lambda: service
    try:
        response = TestClient(app).get("/hub/api/v1/schema-map")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tables"][0]["name"] == "ref_countries"
