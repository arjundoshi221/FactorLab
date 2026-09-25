"""Live v2 ClickHouse schema metadata, described in plain language, for the hub's schema explorer."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from factorlab.api.catalog_text import namespace_text, table_text
from factorlab.storage.clickhouse import ClickHouseStorage


class SchemaMapResult(Protocol):
    column_names: tuple[str, ...] | list[str]
    result_rows: list[tuple[Any, ...]]


class SchemaMapClient(Protocol):
    def query(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> SchemaMapResult: ...


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
    description: str | None = None


class SchemaTable(BaseModel):
    name: str
    namespace: str = ""
    domain: str
    title: str = ""
    summary: str = ""
    notes: list[str] = Field(default_factory=list)
    kind: Literal["table", "view"] = "table"
    engine: str
    stored_rows: int
    bytes_on_disk: int
    primary_key: str
    sorting_key: str
    partition_key: str
    columns: list[SchemaColumn]


class SchemaArea(BaseModel):
    id: str
    title: str
    summary: str


class RelationshipEndpoint(BaseModel):
    table: str
    column: str


RelationshipCategory = Literal["identity", "lineage", "lookup"]


class SchemaRelationship(BaseModel):
    id: str
    source: RelationshipEndpoint
    target: RelationshipEndpoint
    kind: Literal["logical"] = "logical"
    cardinality: Literal["many_to_one"] = "many_to_one"
    optional: bool = False
    enforced: Literal[False] = False
    category: RelationshipCategory = "identity"


class SchemaMapResponse(BaseModel):
    generated_at: datetime
    schema_fingerprint: str
    areas: list[SchemaArea]
    tables: list[SchemaTable]
    relationships: list[SchemaRelationship]
    warnings: list[str] = Field(default_factory=list)


def _rows(result: SchemaMapResult) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]

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

# Links that join almost every table to the same few targets. The explorer hides them by
# default so the business relationships stay readable.
LINEAGE_COLUMNS = frozenset({"raw_id", "ingest_run_id", "computation_id"})
LOOKUP_COLUMNS = frozenset({
    "country_code", "currency_code", "amount_currency", "base_currency", "exchange_code", "source", "source_id",
})


def relationship_category(column: str) -> RelationshipCategory:
    if column in LINEAGE_COLUMNS:
        return "lineage"
    if column in LOOKUP_COLUMNS:
        return "lookup"
    return "identity"


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



def _describe(table: SchemaTable) -> SchemaTable:
    """Attach plain-language titles and descriptions to one table and its columns."""

    text = table_text(table.name)
    table.namespace = table.name.split(".", 1)[0]
    table.title = text.title
    table.summary = text.summary
    table.notes = text.notes
    table.kind = "view" if table.engine in {"View", "MaterializedView"} else "table"
    for column in table.columns:
        column.description = text.columns.get(column.name)
    return table


class SchemaMapRepository:
    """Read live v2 schema metadata across every v2 database."""

    def __init__(self, client: SchemaMapClient, *, databases: tuple[str, ...] = V2_DATABASES) -> None:
        self.client = client
        self.databases = databases

    @classmethod
    def from_environment(cls) -> SchemaMapRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def get_schema_map(self, *, now: datetime | None = None) -> SchemaMapResponse:
        tables = self._tables()
        planned_preview = not tables
        if planned_preview:
            tables = [table.model_copy(deep=True) for table in _planned_v2_tables()]
        else:
            columns_by_table: dict[str, list[SchemaColumn]] = {table.name: [] for table in tables}
            for column in self._columns():
                table_name = str(column["table"])
                if table_name not in columns_by_table:
                    continue
                columns_by_table[table_name].append(SchemaColumn(
                    name=str(column["name"]),
                    type=str(column["type"]),
                    position=int(column["position"]),
                    nullable="Nullable(" in str(column["type"]),
                    default_kind=str(column["default_kind"] or "") or None,
                    default_expression=str(column["default_expression"] or "") or None,
                    in_primary_key=bool(column["is_in_primary_key"]),
                    in_sorting_key=bool(column["is_in_sorting_key"]),
                    in_partition_key=bool(column["is_in_partition_key"]),
                ))
            for table in tables:
                table.columns = sorted(columns_by_table[table.name], key=lambda item: item.position)
        tables = [_describe(table) for table in tables]
        column_lookup = {(table.name, column.name): column for table in tables for column in table.columns}

        fingerprint_payload = [
            (table.name, table.engine, [(column.name, column.type) for column in table.columns])
            for table in tables
        ]
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        warnings: list[str] = []
        if planned_preview:
            warnings.append(
                "Preview mode: these objects come from the bundled migration DDL; row counts remain zero "
                "until the v2 databases are created."
            )
        relationships = [
            SchemaRelationship(
                id=f"{source_table}.{source_column}->{target_table}.{target_column}",
                source=RelationshipEndpoint(table=source_table, column=source_column),
                target=RelationshipEndpoint(table=target_table, column=target_column),
                optional=column_lookup[(source_table, source_column)].nullable,
                category=relationship_category(source_column),
            )
            for source_table, source_column, target_table, target_column in self._v2_relationships(column_lookup)
        ]
        present = {table.namespace for table in tables}
        areas = [
            SchemaArea(id=database, title=namespace_text(database)[0], summary=namespace_text(database)[1])
            for database in self.databases
            if database in present
        ]
        return SchemaMapResponse(
            generated_at=now or datetime.now(UTC),
            schema_fingerprint=fingerprint,
            areas=areas,
            tables=tables,
            relationships=relationships,
            warnings=warnings,
        )

    def _tables(self) -> list[SchemaTable]:
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

    def _columns(self) -> list[dict[str, Any]]:
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


class SchemaMapService:
    """Share one schema read between the deploy gate, the explorer, and repeated page loads."""

    def __init__(
        self, repository: SchemaMapRepository, *, ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: SchemaMapResponse | None = None
        self._expires_at = 0.0

    def get_schema_map(self) -> SchemaMapResponse:
        with self._lock:
            if self._cached is None or self.clock() >= self._expires_at:
                self._cached = self.repository.get_schema_map()
                self._expires_at = self.clock() + self.ttl_seconds
            return self._cached
