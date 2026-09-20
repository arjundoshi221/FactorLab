"""Live ClickHouse schema metadata and shared canvas layout for the private hub."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from factorlab.storage.clickhouse import ClickHouseStorage


class SchemaMapResult(Protocol):
    column_names: tuple[str, ...] | list[str]
    result_rows: list[tuple[Any, ...]]


class SchemaMapClient(Protocol):
    def query(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> SchemaMapResult: ...

    def insert(
        self,
        table: str,
        data: list[list[Any]],
        *,
        column_names: list[str],
    ) -> Any: ...


class SchemaColumn(BaseModel):
    name: str
    type: str
    position: int
    nullable: bool
    default_kind: str | None = None
    default_expression: str | None = None
    in_primary_key: bool = False
    in_sorting_key: bool = False
    in_partition_key: bool = False


class SchemaTable(BaseModel):
    name: str
    domain: str
    engine: str
    stored_rows: int
    bytes_on_disk: int
    primary_key: str
    sorting_key: str
    partition_key: str
    columns: list[SchemaColumn]


class RelationshipEndpoint(BaseModel):
    table: str
    column: str


class SchemaRelationship(BaseModel):
    id: str
    source: RelationshipEndpoint
    target: RelationshipEndpoint
    kind: Literal["logical"] = "logical"
    cardinality: Literal["many_to_one"] = "many_to_one"
    optional: bool = False
    enforced: Literal[False] = False


class LayoutNode(BaseModel):
    table: str
    x: float
    y: float
    collapsed: bool = False


class LayoutViewport(BaseModel):
    x: float = 0
    y: float = 0
    zoom: float = Field(default=1, ge=0.05, le=4)


class SharedSchemaLayout(BaseModel):
    revision: int = 0
    schema_fingerprint: str = ""
    nodes: list[LayoutNode] = Field(default_factory=list)
    viewport: LayoutViewport = Field(default_factory=LayoutViewport)
    updated_at: datetime | None = None


class SchemaMapResponse(BaseModel):
    generated_at: datetime
    database: str
    schema_fingerprint: str
    tables: list[SchemaTable]
    relationships: list[SchemaRelationship]
    layout: SharedSchemaLayout
    warnings: list[str] = Field(default_factory=list)


class SchemaLayoutUpdate(BaseModel):
    base_revision: int = Field(ge=0)
    schema_fingerprint: str
    nodes: list[LayoutNode]
    viewport: LayoutViewport = Field(default_factory=LayoutViewport)


class LayoutConflictError(RuntimeError):
    """The shared layout changed after the caller loaded it."""


class InvalidLayoutError(ValueError):
    """The submitted layout does not match the live schema."""


def _rows(result: SchemaMapResult) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _domain_for(table_name: str) -> str:
    if table_name.startswith("alt_political_"):
        return "Political"
    if table_name.startswith("market_"):
        return "Market data"
    if table_name.startswith("ref_"):
        return "Reference"
    if table_name.startswith("raw_"):
        return "Raw archive"
    if table_name.startswith("india_"):
        return "India operations"
    if table_name.startswith("us_"):
        return "US operations"
    if table_name.startswith("hub_"):
        return "Hub internals"
    if table_name == "ingestion_runs":
        return "Operations"
    return "Unclassified"


# ClickHouse does not enforce foreign keys. These reviewed links describe the
# joins used by FactorLab and are intentionally kept separate from engine keys.
LOGICAL_RELATIONSHIPS: tuple[tuple[str, str, str, str], ...] = (
    ("ref_exchanges", "country_code", "ref_countries", "country_code"),
    ("ref_instruments", "country_code", "ref_countries", "country_code"),
    ("ref_instruments", "exchange_code", "ref_exchanges", "exchange_code"),
    ("ref_instruments", "raw_id", "raw_http_archive", "raw_id"),
    ("ref_contracts", "instrument_id", "ref_instruments", "instrument_id"),
    ("ref_contracts", "raw_id", "raw_http_archive", "raw_id"),
    ("market_candles_1min", "instrument_id", "ref_instruments", "instrument_id"),
    ("market_candles_1min", "contract_id", "ref_contracts", "contract_id"),
    ("market_candles_1min", "raw_id", "raw_http_archive", "raw_id"),
    ("market_candles_daily", "instrument_id", "ref_instruments", "instrument_id"),
    ("market_candles_daily", "contract_id", "ref_contracts", "contract_id"),
    ("market_candles_daily", "raw_id", "raw_http_archive", "raw_id"),
    ("alt_political_legislators", "raw_id", "raw_http_archive", "raw_id"),
    ("alt_political_committees", "parent_committee_id", "alt_political_committees", "committee_id"),
    ("alt_political_committees", "raw_id", "raw_http_archive", "raw_id"),
    ("alt_political_committee_memberships", "committee_id", "alt_political_committees", "committee_id"),
    ("alt_political_committee_memberships", "bioguide_id", "alt_political_legislators", "bioguide_id"),
    ("alt_political_committee_memberships", "raw_id", "raw_http_archive", "raw_id"),
    ("alt_political_house_filings", "bioguide_id", "alt_political_legislators", "bioguide_id"),
    ("alt_political_house_filings", "raw_id", "raw_http_archive", "raw_id"),
    ("alt_political_trades", "country_code", "ref_countries", "country_code"),
    ("alt_political_trades", "filing_id", "alt_political_house_filings", "filing_id"),
    ("alt_political_trades", "bioguide_id", "alt_political_legislators", "bioguide_id"),
    ("alt_political_trades", "raw_id", "raw_http_archive", "raw_id"),
    ("india_expected_series", "instrument_id", "ref_instruments", "instrument_id"),
    ("india_expected_series", "contract_id", "ref_contracts", "contract_id"),
    ("us_expected_series", "instrument_id", "ref_instruments", "instrument_id"),
    ("us_recovery_state", "instrument_id", "ref_instruments", "instrument_id"),
    ("us_session_coverage", "instrument_id", "ref_instruments", "instrument_id"),
)


class SchemaMapRepository:
    """Read live schema metadata and persist the single canonical hub layout."""

    def __init__(self, client: SchemaMapClient, *, database: str = "factorlab") -> None:
        self.client = client
        self.database = database

    @classmethod
    def from_environment(cls) -> SchemaMapRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def get_schema_map(self, *, now: datetime | None = None) -> SchemaMapResponse:
        tables = self._tables()
        columns = self._columns()
        columns_by_table: dict[str, list[SchemaColumn]] = {table.name: [] for table in tables}
        column_lookup: dict[tuple[str, str], SchemaColumn] = {}
        for column in columns:
            table_name = str(column.pop("table"))
            if table_name not in columns_by_table:
                continue
            model = SchemaColumn(
                name=str(column["name"]),
                type=str(column["type"]),
                position=int(column["position"]),
                nullable=str(column["type"]).startswith("Nullable("),
                default_kind=str(column["default_kind"] or "") or None,
                default_expression=str(column["default_expression"] or "") or None,
                in_primary_key=bool(column["is_in_primary_key"]),
                in_sorting_key=bool(column["is_in_sorting_key"]),
                in_partition_key=bool(column["is_in_partition_key"]),
            )
            columns_by_table[table_name].append(model)
            column_lookup[(table_name, model.name)] = model

        for table in tables:
            table.columns = sorted(columns_by_table[table.name], key=lambda item: item.position)

        fingerprint_payload = [
            (table.name, table.engine, [(column.name, column.type) for column in table.columns])
            for table in tables
        ]
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        relationships: list[SchemaRelationship] = []
        warnings: list[str] = []
        for source_table, source_column, target_table, target_column in LOGICAL_RELATIONSHIPS:
            source = column_lookup.get((source_table, source_column))
            target = column_lookup.get((target_table, target_column))
            if source is None or target is None:
                warnings.append(
                    f"Logical relationship unavailable: {source_table}.{source_column} -> "
                    f"{target_table}.{target_column}"
                )
                continue
            relationships.append(
                SchemaRelationship(
                    id=f"{source_table}.{source_column}->{target_table}.{target_column}",
                    source=RelationshipEndpoint(table=source_table, column=source_column),
                    target=RelationshipEndpoint(table=target_table, column=target_column),
                    optional=source.nullable,
                )
            )

        layout = self.get_layout()
        return SchemaMapResponse(
            generated_at=now or datetime.now(UTC),
            database=self.database,
            schema_fingerprint=fingerprint,
            tables=tables,
            relationships=relationships,
            layout=layout,
            warnings=warnings,
        )

    def _tables(self) -> list[SchemaTable]:
        result = self.client.query(
            """
            WITH parts AS (
                SELECT table, sum(rows) AS stored_rows, sum(bytes_on_disk) AS bytes_on_disk
                FROM system.parts
                WHERE active AND database = {database:String}
                GROUP BY table
            )
            SELECT tables.name, tables.engine,
                   ifNull(parts.stored_rows, 0) AS stored_rows,
                   ifNull(parts.bytes_on_disk, 0) AS bytes_on_disk,
                   tables.primary_key, tables.sorting_key, tables.partition_key
            FROM system.tables AS tables
            LEFT JOIN parts ON parts.table = tables.name
            WHERE tables.database = {database:String}
            ORDER BY tables.name
            """,
            parameters={"database": self.database},
        )
        return [
            SchemaTable(
                name=str(row["name"]),
                domain=_domain_for(str(row["name"])),
                engine=str(row["engine"]),
                stored_rows=int(row["stored_rows"] or 0),
                bytes_on_disk=int(row["bytes_on_disk"] or 0),
                primary_key=str(row["primary_key"] or ""),
                sorting_key=str(row["sorting_key"] or ""),
                partition_key=str(row["partition_key"] or ""),
                columns=[],
            )
            for row in _rows(result)
        ]

    def _columns(self) -> list[dict[str, Any]]:
        result = self.client.query(
            """
            SELECT table, name, type, position, default_kind, default_expression,
                   is_in_primary_key, is_in_sorting_key, is_in_partition_key
            FROM system.columns
            WHERE database = {database:String}
            ORDER BY table, position
            """,
            parameters={"database": self.database},
        )
        return _rows(result)

    def get_layout(self) -> SharedSchemaLayout:
        result = self.client.query(
            """
            SELECT revision, schema_fingerprint, layout_json, updated_at
            FROM hub_schema_layouts FINAL
            WHERE layout_id = {layout_id:String}
            LIMIT 1
            """,
            parameters={"layout_id": "default"},
        )
        rows = _rows(result)
        if not rows:
            return SharedSchemaLayout()
        row = rows[0]
        payload = json.loads(str(row["layout_json"]))
        return SharedSchemaLayout(
            revision=int(row["revision"]),
            schema_fingerprint=str(row["schema_fingerprint"]),
            nodes=payload.get("nodes", []),
            viewport=payload.get("viewport", {}),
            updated_at=row["updated_at"],
        )

    def save_layout(
        self,
        *,
        revision: int,
        schema_fingerprint: str,
        nodes: list[LayoutNode],
        viewport: LayoutViewport,
        updated_at: datetime,
    ) -> SharedSchemaLayout:
        payload = json.dumps(
            {
                "nodes": [node.model_dump() for node in nodes],
                "viewport": viewport.model_dump(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        self.client.insert(
            "hub_schema_layouts",
            [["default", revision, schema_fingerprint, payload, updated_at]],
            column_names=[
                "layout_id",
                "revision",
                "schema_fingerprint",
                "layout_json",
                "updated_at",
            ],
        )
        return SharedSchemaLayout(
            revision=revision,
            schema_fingerprint=schema_fingerprint,
            nodes=nodes,
            viewport=viewport,
            updated_at=updated_at,
        )


class SchemaMapService:
    """Serialize canonical layout updates and reject stale or invalid saves."""

    def __init__(self, repository: SchemaMapRepository) -> None:
        self.repository = repository
        self._lock = threading.Lock()

    def get_schema_map(self) -> SchemaMapResponse:
        return self.repository.get_schema_map()

    def save_layout(self, update: SchemaLayoutUpdate) -> SharedSchemaLayout:
        with self._lock:
            schema = self.repository.get_schema_map()
            current = schema.layout
            if update.base_revision != current.revision:
                raise LayoutConflictError(
                    f"Layout revision {current.revision} is newer than {update.base_revision}."
                )
            if update.schema_fingerprint != schema.schema_fingerprint:
                raise InvalidLayoutError("The database schema changed; reload before saving.")

            current_tables = {table.name for table in schema.tables}
            submitted_tables = [node.table for node in update.nodes]
            if len(submitted_tables) != len(set(submitted_tables)):
                raise InvalidLayoutError("Each table may appear only once in a layout.")
            if set(submitted_tables) != current_tables:
                raise InvalidLayoutError("The layout must contain every live table exactly once.")
            values = [
                value
                for node in update.nodes
                for value in (node.x, node.y)
            ] + [update.viewport.x, update.viewport.y, update.viewport.zoom]
            if any(not math.isfinite(value) or abs(value) > 1_000_000 for value in values):
                raise InvalidLayoutError("Layout coordinates must be finite and within bounds.")

            return self.repository.save_layout(
                revision=current.revision + 1,
                schema_fingerprint=schema.schema_fingerprint,
                nodes=update.nodes,
                viewport=update.viewport,
                updated_at=datetime.now(UTC),
            )
