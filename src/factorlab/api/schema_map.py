"""Live ClickHouse schema metadata and shared canvas layout for the private hub."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
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

V2_DATABASES: tuple[str, ...] = (
    "ref",
    "market",
    "fundamentals",
    "alt",
    "book",
    "risk",
    "derived",
    "broker",
    "meta",
    "raw",
    "research",
)

V2_DOMAINS = {
    "ref": "Reference",
    "market": "Market data",
    "fundamentals": "Fundamentals",
    "alt": "Alternative data",
    "book": "Investment book",
    "risk": "Risk",
    "derived": "Derived research",
    "broker": "Broker mirror",
    "meta": "Data operations",
    "raw": "Raw archive",
    "research": "Research views",
}

V2_FK_TARGETS: dict[str, tuple[str, str]] = {
    "country_code": ("ref.countries", "country_code"),
    "currency_code": ("ref.currencies", "currency_code"),
    "amount_currency": ("ref.currencies", "currency_code"),
    "base_currency": ("ref.currencies", "currency_code"),
    "exchange_code": ("ref.exchanges", "exchange_code"),
    "source": ("ref.sources", "source_id"),
    "source_id": ("ref.sources", "source_id"),
    "entity_id": ("ref.entities", "entity_id"),
    "parent_entity_id": ("ref.entities", "entity_id"),
    "child_entity_id": ("ref.entities", "entity_id"),
    "owner_entity_id": ("ref.entities", "entity_id"),
    "opened_by_entity_id": ("ref.entities", "entity_id"),
    "set_by_entity_id": ("ref.entities", "entity_id"),
    "legislator_entity_id": ("ref.entities", "entity_id"),
    "committee_entity_id": ("ref.entities", "entity_id"),
    "security_id": ("ref.securities", "security_id"),
    "underlying_security_id": ("ref.securities", "security_id"),
    "listing_id": ("ref.listings", "listing_id"),
    "underlying_listing_id": ("ref.listings", "listing_id"),
    "old_listing_id": ("ref.listings", "listing_id"),
    "new_listing_id": ("ref.listings", "listing_id"),
    "contract_id": ("ref.contracts", "contract_id"),
    "raw_id": ("raw.archive", "raw_id"),
    "ingest_run_id": ("meta.ingestion_runs", "run_id"),
    "strategy_id": ("book.strategies", "strategy_id"),
    "theme_id": ("book.themes", "theme_id"),
    "parent_theme_id": ("book.themes", "theme_id"),
    "trade_name_id": ("book.trade_names", "trade_name_id"),
    "universe_id": ("ref.universes", "universe_id"),
    "account_id": ("ref.broker_accounts", "account_id"),
    "computation_id": ("derived.computations", "computation_id"),
}


def _split_v2_sql(sql: str) -> list[str]:
    """Split generated ClickHouse DDL without breaking quoted semicolons."""

    statements: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(sql):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
    tail = sql[start:].strip()
    if tail:
        statements.append(tail)
    return statements


def _split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, character in enumerate(value):
        if quote is not None:
            if character == quote and (index == 0 or value[index - 1] != "\\"):
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return [part for part in parts if part]


def _matching_parenthesis(value: str, opening: int) -> int:
    depth = 0
    quote: str | None = None
    for index in range(opening, len(value)):
        character = value[index]
        if quote is not None:
            if character == quote and value[index - 1] != "\\":
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unbalanced CREATE TABLE column list")


def _ddl_clause(statement: str, clause: str) -> str:
    match = re.search(
        rf"\b{clause}\s+(.+?)(?=\s+(?:PRIMARY\s+KEY|ORDER\s+BY|PARTITION\s+BY|SAMPLE\s+BY|TTL|SETTINGS)\b|$)",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _v2_sql_directory() -> Path:
    candidates = (
        Path.cwd() / "sql" / "clickhouse" / "v2",
        Path(__file__).resolve().parents[3] / "sql" / "clickhouse" / "v2",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("The bundled ClickHouse v2 schema directory is unavailable")


@lru_cache(maxsize=1)
def _planned_v2_tables() -> tuple[SchemaTable, ...]:
    """Build the temporary schema preview directly from generated migration DDL."""

    tables: list[SchemaTable] = []
    sql_directory = _v2_sql_directory()
    paths = sorted(sql_directory.glob("wave_*_schema.sql")) + sorted(
        sql_directory.glob("wave_*_views.sql")
    )
    for path in paths:
        sql = re.sub(r"--[^\r\n]*", "", path.read_text(encoding="utf-8"))
        for statement in _split_v2_sql(sql):
            match = re.search(
                r"\bCREATE\s+(TABLE|VIEW)\s+IF\s+NOT\s+EXISTS\s+([a-z_]+\.[a-z_]+)",
                statement,
                flags=re.IGNORECASE,
            )
            if match is None:
                continue
            kind, name = match.group(1).upper(), match.group(2).lower()
            database = name.split(".", 1)[0]
            columns: list[SchemaColumn] = []
            engine = "View" if kind == "VIEW" else ""
            primary_key = ""
            sorting_key = ""
            partition_key = ""
            if kind == "TABLE":
                opening = statement.find("(", match.end())
                closing = _matching_parenthesis(statement, opening)
                for position, definition in enumerate(
                    _split_top_level(statement[opening + 1 : closing]), start=1
                ):
                    column_match = re.match(r"`?([a-zA-Z_][\w]*)`?\s+(.+)", definition, re.DOTALL)
                    if column_match is None:
                        continue
                    column_type = re.split(
                        r"\s+(?:DEFAULT|MATERIALIZED|ALIAS|COMMENT|CODEC|TTL)\b",
                        column_match.group(2).strip(),
                        maxsplit=1,
                        flags=re.IGNORECASE,
                    )[0].strip()
                    columns.append(
                        SchemaColumn(
                            name=column_match.group(1),
                            type=re.sub(r"\s+", " ", column_type),
                            position=position,
                            nullable="Nullable(" in column_type,
                        )
                    )
                remainder = statement[closing + 1 :]
                engine_match = re.search(r"\bENGINE\s*=\s*([^\s]+)", remainder, re.IGNORECASE)
                engine = engine_match.group(1) if engine_match else "Unknown"
                primary_key = _ddl_clause(remainder, r"PRIMARY\s+KEY")
                sorting_key = _ddl_clause(remainder, r"ORDER\s+BY")
                partition_key = _ddl_clause(remainder, r"PARTITION\s+BY")
                primary_names = set(re.findall(r"[a-zA-Z_][\w]*", primary_key))
                sorting_names = set(re.findall(r"[a-zA-Z_][\w]*", sorting_key))
                partition_names = set(re.findall(r"[a-zA-Z_][\w]*", partition_key))
                for column in columns:
                    column.in_primary_key = column.name in primary_names
                    column.in_sorting_key = column.name in sorting_names
                    column.in_partition_key = column.name in partition_names
            tables.append(
                SchemaTable(
                    name=name,
                    domain=V2_DOMAINS.get(database, database),
                    engine=engine,
                    stored_rows=0,
                    bytes_on_disk=0,
                    primary_key=primary_key,
                    sorting_key=sorting_key,
                    partition_key=partition_key,
                    columns=columns,
                )
            )
    return tuple(sorted(tables, key=lambda table: table.name))


class SchemaMapRepository:
    """Read live schema metadata and persist a canonical hub layout."""

    def __init__(
        self,
        client: SchemaMapClient,
        *,
        database: str = "factorlab",
        databases: tuple[str, ...] | None = None,
        qualify_names: bool = False,
        layout_id: str = "default",
    ) -> None:
        self.client = client
        self.database = database
        self.databases = databases or (database,)
        self.qualify_names = qualify_names
        self.layout_id = layout_id

    @classmethod
    def from_environment(cls) -> SchemaMapRepository:
        return cls(ClickHouseStorage.from_environment().client)

    @classmethod
    def v2_from_environment(cls) -> SchemaMapRepository:
        return cls(
            ClickHouseStorage.from_environment().client,
            database="factorlab_v2",
            databases=V2_DATABASES,
            qualify_names=True,
            layout_id="v2",
        )

    def get_schema_map(self, *, now: datetime | None = None) -> SchemaMapResponse:
        tables = self._tables()
        planned_preview = self.qualify_names and not tables
        if planned_preview:
            tables = [table.model_copy(deep=True) for table in _planned_v2_tables()]
            column_lookup = {
                (table.name, column.name): column
                for table in tables
                for column in table.columns
            }
        else:
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
                    nullable="Nullable(" in str(column["type"]),
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
        if planned_preview:
            warnings.append(
                "Preview mode: these objects come from the bundled migration DDL; row counts remain zero "
                "until the v2 databases are created."
            )
        relationship_specs = (
            self._v2_relationships(column_lookup) if self.qualify_names else LOGICAL_RELATIONSHIPS
        )
        for source_table, source_column, target_table, target_column in relationship_specs:
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
        if self.qualify_names:
            result = self.client.query(
                """
                WITH parts AS (
                    SELECT database, table, sum(rows) AS stored_rows,
                           sum(bytes_on_disk) AS bytes_on_disk
                    FROM system.parts
                    WHERE active AND has({databases:Array(String)}, database)
                    GROUP BY database, table
                )
                SELECT tables.database, tables.name, tables.engine,
                       ifNull(parts.stored_rows, 0) AS stored_rows,
                       ifNull(parts.bytes_on_disk, 0) AS bytes_on_disk,
                       tables.primary_key, tables.sorting_key, tables.partition_key
                FROM system.tables AS tables
                LEFT JOIN parts
                  ON parts.database = tables.database AND parts.table = tables.name
                WHERE has({databases:Array(String)}, tables.database)
                ORDER BY tables.database, tables.name
                """,
                parameters={"databases": list(self.databases)},
            )
            return [
                SchemaTable(
                    name=f"{row['database']}.{row['name']}",
                    domain=V2_DOMAINS.get(str(row["database"]), str(row["database"])),
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
        if self.qualify_names:
            result = self.client.query(
                """
                SELECT database, table, name, type, position, default_kind, default_expression,
                       is_in_primary_key, is_in_sorting_key, is_in_partition_key
                FROM system.columns
                WHERE has({databases:Array(String)}, database)
                ORDER BY database, table, position
                """,
                parameters={"databases": list(self.databases)},
            )
            rows = _rows(result)
            for row in rows:
                row["table"] = f"{row['database']}.{row['table']}"
            return rows
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

    def _v2_relationships(
        self, column_lookup: dict[tuple[str, str], SchemaColumn]
    ) -> tuple[tuple[str, str, str, str], ...]:
        relationships: set[tuple[str, str, str, str]] = set()
        for source_table, source_column in column_lookup:
            target = V2_FK_TARGETS.get(source_column)
            if target is None:
                continue
            target_table, target_column = target
            if source_table == target_table and source_column == target_column:
                continue
            if (target_table, target_column) in column_lookup:
                relationships.add((source_table, source_column, target_table, target_column))
        return tuple(sorted(relationships))

    def get_layout(self) -> SharedSchemaLayout:
        result = self.client.query(
            """
            SELECT revision, schema_fingerprint, layout_json, updated_at
            FROM hub_schema_layouts FINAL
            WHERE layout_id = {layout_id:String}
            LIMIT 1
            """,
            parameters={"layout_id": self.layout_id},
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
            [[self.layout_id, revision, schema_fingerprint, payload, updated_at]],
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
