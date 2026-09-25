"""The hub's data catalog: plain-language schemas, row previews, and pipeline health."""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from factorlab.api.catalog_pipelines import PIPELINES, PIPELINES_BY_ID, PipelineInfo, writers_of
from factorlab.api.catalog_query import (
    CSV_CELL_CHARS,
    JSON_CELL_CHARS,
    MAX_LIMIT,
    OPERATORS,
    STATS_SAMPLE_ROWS,
    CatalogError,
    CatalogNotFound,
    ColumnInfo,
    QuerySlots,
    RowsRequest,
    TableInfo,
    build_activity_query,
    build_rows_query,
    build_stats_query,
    clean_row,
    country_filter,
    country_of,
    csv_max_rows,
    describe_request,
    ensure_preview_allowed,
    env_flag,
    friendly_type,
    hidden_columns,
    max_window_width,
    preview_denied,
    query_settings,
    resolve_window,
    scrub_text,
    translate_database_error,
    type_class,
    validate_table_name,
)
from factorlab.api.hub import (
    V2_TABLE_PROFILES,
    HubOverviewService,
    HubStatus,
    HubTable,
    _default_profile,
)
from factorlab.api.schema_map import (
    V2_DATABASES,
    V2_DOMAINS,
    SchemaMapRepository,
    SchemaMapResponse,
    SchemaTable,
)

SCHEMA_TTL_SECONDS = 600
ROWS_TTL_SECONDS = 30
STATS_TTL_SECONDS = 1_800
ACTIVITY_TTL_SECONDS = 600
PIPELINES_TTL_SECONDS = 60
MARKETS_TTL_SECONDS = 600
PROBLEM_STATUSES = ("failed", "partial")
MARKET_LABELS = {"IN": "India", "US": "US", "*": "Global"}
PIPELINE_COUNTRIES = {"India": "IN", "US": "US", "Political": "US"}
# Namespaces whose populated tables are split into per-market figures. raw.archive is left out:
# its payload-heavy parts make a full scan too costly for a catalog refresh.
MARKET_NAMESPACES = frozenset({"market", "meta", "ref", "alt"})


# ── Response models ──────────────────────────────────────────────────────────


class CatalogNamespace(BaseModel):
    id: str
    title: str
    summary: str
    table_count: int
    populated_tables: int
    view_count: int
    stored_rows: int
    bytes_on_disk: int
    status_counts: dict[str, int]


class MarketSlice(BaseModel):
    """One market's share of a table that stores several countries together."""

    country_code: str
    label: str
    stored_rows: int
    first_data_at: str | None
    last_data_at: str | None
    last_ingested_at: datetime | None


class CatalogTableSummary(BaseModel):
    name: str
    namespace: str
    title: str
    summary: str
    kind: Literal["table", "view"]
    engine: str
    stored_rows: int
    bytes_on_disk: int
    first_data_at: str | None
    last_data_at: str | None
    last_ingested_at: datetime | None
    status: HubStatus
    status_reason: str
    column_count: int
    columns: list[str]
    column_notes: dict[str, str]
    previewable: bool
    markets: list[MarketSlice] = []


class CatalogIndex(BaseModel):
    generated_at: datetime
    namespaces: list[CatalogNamespace]
    tables: list[CatalogTableSummary]
    previews_enabled: bool
    csv_enabled: bool


class CatalogColumn(BaseModel):
    name: str
    type: str
    friendly_type: str
    type_class: str
    description: str | None
    nullable: bool
    in_primary_key: bool
    in_sorting_key: bool
    in_partition_key: bool
    hidden: bool
    operators: list[str]


class CatalogRelation(BaseModel):
    direction: Literal["out", "in"]
    column: str
    table: str
    table_title: str
    target_column: str


class CatalogWriter(BaseModel):
    id: str
    label: str
    schedule: str


class CatalogKeys(BaseModel):
    primary_key: str
    sorting_key: str
    partition_key: str
    explanation: list[str]


class CatalogPreviewPolicy(BaseModel):
    enabled: bool
    reason: str | None
    csv_enabled: bool
    windowed: bool
    time_column: str | None
    default_start: datetime | None
    default_end: datetime | None
    max_window_days: int
    latest_version_only: bool
    hidden_columns: list[str]
    max_csv_rows: int
    max_page_rows: int


class CatalogTableDetail(CatalogTableSummary):
    design_notes: str | None
    notes: list[str]
    keys: CatalogKeys
    column_details: list[CatalogColumn]
    related: list[CatalogRelation]
    writers: list[CatalogWriter]
    preview: CatalogPreviewPolicy


class RowsPage(BaseModel):
    table: str
    columns: list[str]
    rows: list[list[Any]]
    offset: int
    limit: int
    has_more: bool
    window_start: datetime | None
    window_end: datetime | None
    sort: str | None
    descending: bool
    latest_version_only: bool
    truncated_cells: int
    notes: list[str]


class ColumnStat(BaseModel):
    name: str
    nulls_percent: float | None
    distinct_approx: int | None
    min: str | None
    max: str | None
    top_values: list[str]


class TableStats(BaseModel):
    table: str
    generated_at: datetime
    sample_rows: int
    sample_limit: int
    window_start: datetime | None
    window_end: datetime | None
    columns: list[ColumnStat]


class PipelineRun(BaseModel):
    run_id: str
    pipeline: str
    source: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    rows_written: int
    requested_series: int
    successful_series: int
    failed_series: int
    error: str | None


class ActivityBucket(BaseModel):
    bucket: date
    rows: int


class TableActivity(BaseModel):
    table: str
    grain: Literal["day", "month"]
    time_column: str | None
    buckets: list[ActivityBucket]
    runs: list[PipelineRun]


class PipelineDay(BaseModel):
    day: date
    success: int
    problem: int
    total: int


class PipelineCard(BaseModel):
    id: str
    label: str
    market: str
    source: str
    description: str
    schedule: str
    scheduled: bool
    per_symbol_runs: bool
    tables_written: list[str]
    status: HubStatus
    status_reason: str
    last_status: str | None
    last_started_at: datetime | None
    last_completed_at: datetime | None
    runs_24h: int
    success_24h: int
    problem_24h: int
    runs_7d: int
    success_7d: int
    problem_7d: int
    rows_24h: int
    days: list[PipelineDay]
    recent_problems: list[PipelineRun]


class SourceHealth(BaseModel):
    country_code: str
    source: str
    status: str
    detail: str
    checked_at: datetime | None


class PipelinesResponse(BaseModel):
    generated_at: datetime
    pipelines: list[PipelineCard]
    sources: list[SourceHealth]


class PipelineRunsPage(BaseModel):
    pipeline: str
    items: list[PipelineRun]
    limit: int
    offset: int
    has_more: bool


# ── Descriptions ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _descriptions() -> tuple[dict[str, Any], dict[str, Any]]:
    directory = Path(__file__).resolve().parent
    generated = json.loads((directory / "catalog_descriptions.json").read_text(encoding="utf-8"))
    curated = json.loads((directory / "catalog_curated.json").read_text(encoding="utf-8"))
    return generated.get("tables", {}), curated


def _humanize(name: str) -> str:
    text = name.split(".", 1)[-1].replace("_", " ").strip()
    return text[:1].upper() + text[1:]


@dataclass(frozen=True)
class TableText:
    title: str
    summary: str
    design_notes: str | None
    notes: list[str]
    columns: dict[str, str]


def table_text(name: str) -> TableText:
    generated, curated = _descriptions()
    design = generated.get(name, {})
    manual = curated.get("tables", {}).get(name, {})
    columns = {**design.get("columns", {}), **manual.get("columns", {})}
    design_notes = design.get("description")
    summary = manual.get("summary") or design_notes or "No description has been written for this table yet."
    return TableText(
        title=manual.get("title") or _humanize(name),
        summary=summary,
        design_notes=design_notes if design_notes and design_notes != summary else None,
        notes=list(manual.get("notes", [])),
        columns=columns,
    )


def namespace_text(namespace: str) -> tuple[str, str]:
    _, curated = _descriptions()
    entry = curated.get("namespaces", {}).get(namespace, {})
    return entry.get("title") or V2_DOMAINS.get(namespace, namespace), entry.get("summary", "")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5_400:
        return f"{round(seconds / 60)} min"
    if seconds < 172_800:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86_400)} days"


def _time_column(name: str, columns: list[str]) -> str | None:
    profile = V2_TABLE_PROFILES.get(name) or _default_profile(name, columns)
    if profile is None:
        return None
    return profile.first_expression if profile.first_expression in columns else None


def _key_explanation(table: SchemaTable) -> list[str]:
    lines: list[str] = []
    if table.engine in {"View", "MaterializedView"}:
        return ["A view stores no rows; it reads from other tables when queried."]
    if table.sorting_key:
        if "Replacing" in table.engine:
            lines.append(
                f"Each row is identified by ({table.sorting_key}). When a row is re-collected, "
                "the newer version replaces the older one."
            )
        else:
            lines.append(f"Rows are stored in order of ({table.sorting_key}) and are never replaced.")
    if table.partition_key:
        lines.append(f"Data is split into storage partitions by {table.partition_key}.")
    return lines


def _csv_safe(value: Any) -> Any:
    # Keep spreadsheet apps from treating vendor text as formulas.
    if isinstance(value, str) and value[:1] in {"=", "+", "@", "\t", "\r"}:
        return f"'{value}"
    if isinstance(value, str) and value[:1] == "-" and not value[1:2].isdigit():
        return f"'{value}"
    return value


def _session_open(calendar_code: str | None, now: datetime) -> bool:
    if calendar_code is None:
        return True
    import exchange_calendars as xcals
    import pandas as pd

    calendar = _calendar(calendar_code, xcals)
    try:
        return bool(calendar.is_open_on_minute(pd.Timestamp(now.astimezone(UTC))))
    except (ValueError, KeyError):
        return False


_CALENDARS: dict[str, Any] = {}


def _calendar(code: str, xcals: Any) -> Any:
    if code not in _CALENDARS:
        _CALENDARS[code] = xcals.get_calendar(code)
    return _CALENDARS[code]


class _TTLCache:
    def __init__(self, maxsize: int = 128) -> None:
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.maxsize = maxsize

    def get(self, key: Any, now: float) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None or item[0] <= now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return item[1]

    def put(self, key: Any, value: Any, expires: float) -> None:
        with self._lock:
            self._items[key] = (expires, value)
            self._items.move_to_end(key)
            while len(self._items) > self.maxsize:
                self._items.popitem(last=False)


def _run_from_row(row: dict[str, Any]) -> PipelineRun:
    error = row.get("error")
    return PipelineRun(
        run_id=str(row["run_id"]),
        pipeline=str(row["pipeline"]),
        source=str(row.get("source") or ""),
        status=str(row["status"]),
        started_at=_as_datetime(row["started_at"]) or datetime.min.replace(tzinfo=UTC),
        completed_at=_as_datetime(row.get("completed_at")),
        rows_written=int(row.get("rows_written") or 0),
        requested_series=int(row.get("requested_series") or 0),
        successful_series=int(row.get("successful_series") or 0),
        failed_series=int(row.get("failed_series") or 0),
        error=scrub_text(str(error), limit=JSON_CELL_CHARS)[0] if error else None,
    )


RUN_COLUMNS = (
    "run_id, pipeline, source, status, started_at, completed_at, rows_written, "
    "requested_series, successful_series, failed_series, error"
)


# ── Service ──────────────────────────────────────────────────────────────────


class CatalogService:
    """Serve the catalog from cached metadata and bounded, read-only queries."""

    def __init__(
        self,
        client: Any,
        overview: HubOverviewService,
        *,
        schema_repository: SchemaMapRepository | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        slots: QuerySlots | None = None,
    ) -> None:
        self.client = client
        self.overview = overview
        self.schema_repository = schema_repository or SchemaMapRepository(
            client, database="factorlab_v2", databases=V2_DATABASES, qualify_names=True, layout_id="v2"
        )
        self.clock = clock
        self.now = now
        self.slots = slots or QuerySlots(3)
        self._cache = _TTLCache()
        self._schema_lock = threading.Lock()
        self._markets_lock = threading.Lock()

    # metadata ---------------------------------------------------------------

    def _schema(self) -> SchemaMapResponse:
        cached = self._cache.get("schema", self.clock())
        if cached is not None:
            return cached
        with self._schema_lock:
            cached = self._cache.get("schema", self.clock())
            if cached is None:
                cached = self.schema_repository.get_schema_map()
                self._cache.put("schema", cached, self.clock() + SCHEMA_TTL_SECONDS)
            return cached

    def _overview_tables(self) -> dict[str, HubTable]:
        return {table.name: table for table in self.overview.get_overview().tables}

    def _schema_table(self, name: str) -> SchemaTable:
        validate_table_name(name)
        table = next((item for item in self._schema().tables if item.name == name), None)
        if table is None:
            raise CatalogNotFound("Table not found.")
        return table

    def _table_info(self, schema_table: SchemaTable, hub: HubTable | None) -> TableInfo:
        names = [column.name for column in schema_table.columns]
        return TableInfo(
            name=schema_table.name,
            engine=schema_table.engine,
            columns=tuple(ColumnInfo(column.name, column.type) for column in schema_table.columns),
            stored_rows=hub.stored_rows if hub else schema_table.stored_rows,
            time_column=_time_column(schema_table.name, names),
            last_data_at=_as_datetime(hub.last_data_at) if hub else None,
        )

    def table_info(self, name: str, country: str | None = None) -> TableInfo:
        schema_table = self._schema_table(name)
        info = self._table_info(schema_table, self._overview_tables().get(name))
        if country is not None:
            # Anchor time windows on this market's newest row, not the table's overall newest row.
            market = next((item for item in self._markets().get(name, []) if item.country_code == country), None)
            if market is not None and market.last_data_at:
                info = replace(info, last_data_at=_as_datetime(market.last_data_at))
        return info

    def _markets(self) -> dict[str, list[MarketSlice]]:
        """Per-market rows, coverage, and freshness for populated tables with a country_code column."""

        cached = self._cache.get("markets", self.clock())
        if cached is not None:
            return cached
        with self._markets_lock:
            cached = self._cache.get("markets", self.clock())
            if cached is not None:
                return cached
            hub_tables = self._overview_tables()
            markets: dict[str, list[MarketSlice]] = {}
            for table in self._schema().tables:
                names = [column.name for column in table.columns]
                hub = hub_tables.get(table.name)
                if (
                    "country_code" not in names
                    or table.name.split(".", 1)[0] not in MARKET_NAMESPACES
                    or table.engine in {"View", "MaterializedView"}
                    or not (hub.stored_rows if hub else table.stored_rows)
                ):
                    continue
                time_column = _time_column(table.name, names)
                first = f"min(`{time_column}`)" if time_column else "NULL"
                last = f"max(`{time_column}`)" if time_column else "NULL"
                ingested = "max(`ingested_at`)" if "ingested_at" in names else "NULL"
                try:
                    result = self._query(
                        f"SELECT toString(country_code) AS country, count() AS rows, {first} AS first_data_at, "
                        f"{last} AS last_data_at, {ingested} AS last_ingested_at "
                        f"FROM {table.name} GROUP BY country ORDER BY rows DESC LIMIT 20",
                        {}, query_settings("markets", max_rows=20),
                    )
                except CatalogError:
                    continue
                slices = [
                    MarketSlice(
                        country_code=str(row["country"]).strip(),
                        label=MARKET_LABELS.get(str(row["country"]).strip(), str(row["country"]).strip()),
                        stored_rows=int(row["rows"]),
                        first_data_at=_iso(row["first_data_at"]),
                        last_data_at=_iso(row["last_data_at"]),
                        last_ingested_at=_as_datetime(row["last_ingested_at"]),
                    )
                    for row in _rows(result)
                    if str(row["country"]).strip()
                ]
                if slices:
                    markets[table.name] = slices
            self._cache.put("markets", markets, self.clock() + MARKETS_TTL_SECONDS)
            return markets

    def _summary(self, schema_table: SchemaTable, hub: HubTable | None) -> CatalogTableSummary:
        text = table_text(schema_table.name)
        is_view = schema_table.engine in {"View", "MaterializedView"}
        column_names = [column.name for column in schema_table.columns]
        return CatalogTableSummary(
            name=schema_table.name,
            namespace=schema_table.name.split(".", 1)[0],
            title=text.title,
            summary=text.summary,
            kind="view" if is_view else "table",
            engine=schema_table.engine,
            stored_rows=hub.stored_rows if hub else schema_table.stored_rows,
            bytes_on_disk=hub.bytes_on_disk if hub else schema_table.bytes_on_disk,
            first_data_at=hub.first_data_at if hub else None,
            last_data_at=hub.last_data_at if hub else None,
            last_ingested_at=hub.last_ingested_at if hub else None,
            status=hub.today_status if hub else "unknown",
            status_reason=hub.status_reason if hub else "Health has not been evaluated.",
            column_count=len(column_names),
            columns=column_names,
            column_notes={key: value for key, value in text.columns.items() if key in column_names},
            previewable=not is_view and env_flag("FACTORLAB_CATALOG_PREVIEW") and not preview_denied(schema_table.name),
            markets=self._markets().get(schema_table.name, []),
        )

    def index(self) -> CatalogIndex:
        hub_tables = self._overview_tables()
        summaries = [self._summary(table, hub_tables.get(table.name)) for table in self._schema().tables]
        namespaces: list[CatalogNamespace] = []
        for namespace in V2_DATABASES:
            members = [item for item in summaries if item.namespace == namespace]
            if not members:
                continue
            stored = [item for item in members if item.kind == "table"]
            counts: dict[str, int] = {}
            for item in stored:
                counts[item.status] = counts.get(item.status, 0) + 1
            title, summary = namespace_text(namespace)
            namespaces.append(CatalogNamespace(
                id=namespace, title=title, summary=summary, table_count=len(stored),
                populated_tables=sum(item.stored_rows > 0 for item in stored),
                view_count=len(members) - len(stored),
                stored_rows=sum(item.stored_rows for item in stored),
                bytes_on_disk=sum(item.bytes_on_disk for item in stored),
                status_counts=counts,
            ))
        return CatalogIndex(
            generated_at=self.now(),
            namespaces=namespaces,
            tables=summaries,
            previews_enabled=env_flag("FACTORLAB_CATALOG_PREVIEW"),
            csv_enabled=env_flag("FACTORLAB_CATALOG_PREVIEW") and env_flag("FACTORLAB_CATALOG_CSV"),
        )

    def table_detail(self, name: str) -> CatalogTableDetail:
        schema = self._schema()
        schema_table = self._schema_table(name)
        hub = self._overview_tables().get(name)
        info = self._table_info(schema_table, hub)
        summary = self._summary(schema_table, hub)
        text = table_text(name)
        hidden = set(hidden_columns(info))
        titles = {table.name: table_text(table.name).title for table in schema.tables}
        related = [
            CatalogRelation(direction="out", column=item.source.column, table=item.target.table,
                            table_title=titles.get(item.target.table, item.target.table),
                            target_column=item.target.column)
            for item in schema.relationships if item.source.table == name
        ] + [
            CatalogRelation(direction="in", column=item.target.column, table=item.source.table,
                            table_title=titles.get(item.source.table, item.source.table),
                            target_column=item.source.column)
            for item in schema.relationships if item.target.table == name
        ]
        reason: str | None = None
        try:
            ensure_preview_allowed(info)
        except CatalogError as exc:
            reason = str(exc)
        window = None
        if reason is None and info.windowed:
            window = resolve_window(info, None, None, now=self.now())
        return CatalogTableDetail(
            **summary.model_dump(),
            design_notes=text.design_notes,
            notes=text.notes,
            keys=CatalogKeys(
                primary_key=schema_table.primary_key, sorting_key=schema_table.sorting_key,
                partition_key=schema_table.partition_key, explanation=_key_explanation(schema_table),
            ),
            column_details=[
                CatalogColumn(
                    name=column.name, type=column.type, friendly_type=friendly_type(column.type),
                    type_class=type_class(column.type), description=text.columns.get(column.name),
                    nullable=column.nullable, in_primary_key=column.in_primary_key,
                    in_sorting_key=column.in_sorting_key, in_partition_key=column.in_partition_key,
                    hidden=column.name in hidden,
                    operators=[] if column.name in hidden else sorted(OPERATORS[type_class(column.type)]),
                )
                for column in schema_table.columns
            ],
            related=sorted(related, key=lambda item: (item.direction, item.table, item.column)),
            writers=[CatalogWriter(id=item.id, label=item.label, schedule=item.schedule) for item in writers_of(name)],
            preview=CatalogPreviewPolicy(
                enabled=reason is None, reason=reason,
                csv_enabled=reason is None and env_flag("FACTORLAB_CATALOG_CSV"),
                windowed=info.windowed, time_column=info.time_column,
                default_start=window.start if window else None, default_end=window.end if window else None,
                max_window_days=max_window_width(name).days, latest_version_only=info.uses_final,
                hidden_columns=sorted(hidden), max_csv_rows=csv_max_rows(), max_page_rows=MAX_LIMIT,
            ),
        )

    # bounded data queries ---------------------------------------------------

    def _query(self, sql: str, parameters: dict[str, Any], settings: dict[str, Any]) -> Any:
        try:
            return self.client.query(sql, parameters=parameters, settings=settings)
        except CatalogError:
            raise
        except Exception as exc:  # never echo database errors to the public hub
            raise translate_database_error(exc) from exc

    def rows(self, name: str, request: RowsRequest) -> RowsPage:
        info = self.table_info(name, country_of(request.filters))
        ensure_preview_allowed(info)
        built = build_rows_query(info, request, now=self.now())
        key = ("rows", built.sql, json.dumps(built.parameters, sort_keys=True, default=str))
        cached = self._cache.get(key, self.clock())
        if cached is not None:
            return cached
        self.slots.acquire()
        try:
            result = self._query(built.sql, built.parameters, built.settings)
        finally:
            self.slots.release()
        limit = max(1, min(request.limit, MAX_LIMIT))
        truncated = 0
        rows: list[list[Any]] = []
        for row in list(result.result_rows)[:limit]:
            values, cut = clean_row(row, built.columns, table=name, limit=JSON_CELL_CHARS)
            rows.append(values)
            truncated += cut
        page = RowsPage(
            table=name, columns=built.columns, rows=rows, offset=max(0, request.offset), limit=limit,
            has_more=len(result.result_rows) > limit,
            window_start=built.window.start if built.window else None,
            window_end=built.window.end if built.window else None,
            sort=request.sort or info.time_column, descending=request.descending,
            latest_version_only=built.final_applied, truncated_cells=truncated, notes=built.notes,
        )
        self._cache.put(key, page, self.clock() + ROWS_TTL_SECONDS)
        return page

    def csv_export(self, name: str, request: RowsRequest) -> tuple[str, dict[str, str], Iterator[str]]:
        country = country_of(request.filters)
        info = self.table_info(name, country)
        ensure_preview_allowed(info, csv=True)
        built = build_rows_query(info, request, now=self.now(), kind="csv")
        cap = csv_max_rows()
        self.slots.acquire()
        try:
            stream = self.client.query_row_block_stream(
                built.sql, parameters=built.parameters, settings=built.settings
            )
        except Exception as exc:
            self.slots.release()
            if isinstance(exc, CatalogError):
                raise
            raise translate_database_error(exc) from exc

        def generate() -> Iterator[str]:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            written = 0
            try:
                writer.writerow(built.columns)
                with stream as blocks:
                    for block in blocks:
                        for row in block:
                            values, _ = clean_row(row, built.columns, table=name, limit=CSV_CELL_CHARS)
                            writer.writerow([_csv_safe(value) for value in values])
                            written += 1
                            if written >= cap:
                                break
                        yield buffer.getvalue()
                        buffer.seek(0)
                        buffer.truncate()
                        if written >= cap:
                            break
                yield buffer.getvalue()
            finally:
                self.slots.release()

        filename = f"{name}{f'_{country.lower()}' if country else ''}_{self.now():%Y-%m-%d}.csv"
        return filename, dict(describe_request(request, built.window)), generate()

    def stats(self, name: str, country: str | None = None) -> TableStats:
        info = self.table_info(name, country)
        ensure_preview_allowed(info)
        filters = country_filter(info, country)
        key = ("stats", name, country)
        cached = self._cache.get(key, self.clock())
        if cached is not None:
            return cached
        built = build_stats_query(info, now=self.now(), filters=filters)
        self.slots.acquire()
        try:
            result = self._query(built.sql, built.parameters, built.settings)
        finally:
            self.slots.release()
        row = _rows(result)[0] if result.result_rows else {}
        sample = int(row.get("__rows") or 0)
        columns: list[ColumnStat] = []
        for column in built.columns:
            nulls = row.get(f"{column}__nulls")
            distinct = row.get(f"{column}__distinct")
            top = row.get(f"{column}__top") or []

            def text(value: Any) -> str | None:
                return None if value is None else scrub_text(str(value), limit=80)[0]

            columns.append(ColumnStat(
                name=column,
                nulls_percent=round(int(nulls) * 100 / sample, 2) if sample and nulls is not None else None,
                distinct_approx=int(distinct) if distinct is not None else None,
                min=text(row.get(f"{column}__min")),
                max=text(row.get(f"{column}__max")),
                top_values=[scrub_text(str(value), limit=64)[0] for value in top][:5],
            ))
        stats = TableStats(
            table=name, generated_at=self.now(), sample_rows=sample, sample_limit=STATS_SAMPLE_ROWS,
            window_start=built.window.start if built.window else None,
            window_end=built.window.end if built.window else None, columns=columns,
        )
        self._cache.put(key, stats, self.clock() + STATS_TTL_SECONDS)
        return stats

    def activity(self, name: str, grain: Literal["day", "month"], country: str | None = None) -> TableActivity:
        info = self.table_info(name, country)
        if info.is_view:
            raise CatalogError("Views have no stored rows to chart.")
        filters = country_filter(info, country)
        key = ("activity", name, grain, country)
        cached = self._cache.get(key, self.clock())
        if cached is not None:
            return cached
        buckets: list[ActivityBucket] = []
        if info.time_column and info.stored_rows:
            built = build_activity_query(info, grain, now=self.now(), filters=filters)
            self.slots.acquire()
            try:
                result = self._query(built.sql, built.parameters, built.settings)
            finally:
                self.slots.release()
            buckets = [
                ActivityBucket(bucket=row["bucket"], rows=int(row["rows"]))
                for row in _rows(result)
                if row["bucket"] is not None
            ]
        pipelines = [
            item.id for item in writers_of(name)
            if country is None or PIPELINE_COUNTRIES.get(item.market) == country
        ]
        runs: list[PipelineRun] = []
        if pipelines:
            result = self._query(
                f"SELECT {RUN_COLUMNS} FROM meta.ingestion_runs FINAL "
                "WHERE has({pipelines:Array(String)}, pipeline) "
                "AND started_at >= parseDateTime64BestEffort({since:String}, 3, 'UTC') "
                "ORDER BY started_at DESC LIMIT 10",
                {"pipelines": pipelines, "since": (self.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")},
                query_settings("rows", max_rows=10),
            )
            runs = [_run_from_row(row) for row in _rows(result)]
        activity = TableActivity(table=name, grain=grain, time_column=info.time_column, buckets=buckets, runs=runs)
        self._cache.put(key, activity, self.clock() + ACTIVITY_TTL_SECONDS)
        return activity

    # pipelines ---------------------------------------------------------------

    def pipelines(self) -> PipelinesResponse:
        cached = self._cache.get("pipelines", self.clock())
        if cached is not None:
            return cached
        now = self.now()
        since_week = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        since_day = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        ids = [pipeline.id for pipeline in PIPELINES]
        settings = query_settings("rows", max_rows=10_000)
        window = (
            "has({pipelines:Array(String)}, pipeline) "
            "AND started_at >= parseDateTime64BestEffort({week:String}, 3, 'UTC')"
        )
        parameters = {"pipelines": ids, "week": since_week, "day": since_day}
        day_start = "parseDateTime64BestEffort({day:String}, 3, 'UTC')"
        summary = _rows(self._query(
            f"""
            SELECT pipeline,
                   argMax(status, started_at) AS last_status,
                   max(started_at) AS last_started_at,
                   argMax(completed_at, started_at) AS last_completed_at,
                   countIf(started_at >= {day_start}) AS runs_24h,
                   countIf(started_at >= {day_start} AND status = 'success') AS success_24h,
                   countIf(started_at >= {day_start} AND status IN ('failed', 'partial')) AS problem_24h,
                   count() AS runs_7d,
                   countIf(status = 'success') AS success_7d,
                   countIf(status IN ('failed', 'partial')) AS problem_7d,
                   sumIf(rows_written, started_at >= {day_start}) AS rows_24h
            FROM meta.ingestion_runs FINAL
            WHERE {window}
            GROUP BY pipeline
            """,
            parameters, settings,
        ))
        days = _rows(self._query(
            f"""
            SELECT pipeline, toDate(started_at) AS day,
                   countIf(status = 'success') AS success,
                   countIf(status IN ('failed', 'partial')) AS problem,
                   count() AS total
            FROM meta.ingestion_runs FINAL
            WHERE {window}
            GROUP BY pipeline, day
            ORDER BY pipeline, day
            """,
            parameters, settings,
        ))
        problems = _rows(self._query(
            f"""
            SELECT {RUN_COLUMNS}
            FROM meta.ingestion_runs FINAL
            WHERE {window} AND status IN ('failed', 'partial')
            ORDER BY started_at DESC
            LIMIT 5 BY pipeline
            """,
            parameters, settings,
        ))
        try:
            sources = [
                SourceHealth(
                    country_code=str(row["country_code"]), source=str(row["source"]),
                    status=str(row["status"]),
                    detail=scrub_text(str(row.get("detail") or ""), limit=JSON_CELL_CHARS)[0],
                    checked_at=_as_datetime(row.get("checked_at")),
                )
                for row in _rows(self._query(
                    "SELECT country_code, source, status, detail, checked_at FROM meta.source_status FINAL "
                    "ORDER BY country_code, source",
                    {}, settings,
                ))
            ]
        except CatalogError:
            sources = []
        by_pipeline = {row["pipeline"]: row for row in summary}
        cards = [
            self._pipeline_card(
                pipeline, by_pipeline.get(pipeline.id),
                [row for row in days if row["pipeline"] == pipeline.id],
                [_run_from_row(row) for row in problems if row["pipeline"] == pipeline.id],
                now,
            )
            for pipeline in PIPELINES
        ]
        response = PipelinesResponse(generated_at=now, pipelines=cards, sources=sources)
        self._cache.put("pipelines", response, self.clock() + PIPELINES_TTL_SECONDS)
        return response

    def _pipeline_card(
        self,
        pipeline: PipelineInfo,
        summary: dict[str, Any] | None,
        days: list[dict[str, Any]],
        problems: list[PipelineRun],
        now: datetime,
    ) -> PipelineCard:
        values = summary or {}
        last_started = _as_datetime(values.get("last_started_at"))
        status, reason = pipeline_status(pipeline, values, last_started, problems, now)
        return PipelineCard(
            id=pipeline.id, label=pipeline.label, market=pipeline.market, source=pipeline.source,
            description=pipeline.description, schedule=pipeline.schedule, scheduled=pipeline.scheduled,
            per_symbol_runs=pipeline.per_symbol_runs, tables_written=list(pipeline.tables_written),
            status=status, status_reason=reason,
            last_status=str(values["last_status"]) if values.get("last_status") else None,
            last_started_at=last_started, last_completed_at=_as_datetime(values.get("last_completed_at")),
            runs_24h=int(values.get("runs_24h") or 0), success_24h=int(values.get("success_24h") or 0),
            problem_24h=int(values.get("problem_24h") or 0), runs_7d=int(values.get("runs_7d") or 0),
            success_7d=int(values.get("success_7d") or 0), problem_7d=int(values.get("problem_7d") or 0),
            rows_24h=int(values.get("rows_24h") or 0),
            days=[
                PipelineDay(day=row["day"], success=int(row["success"]), problem=int(row["problem"]),
                            total=int(row["total"]))
                for row in days
            ],
            recent_problems=problems,
        )

    def pipeline_runs(self, pipeline_id: str, *, status: str | None, limit: int, offset: int) -> PipelineRunsPage:
        if pipeline_id not in PIPELINES_BY_ID:
            raise CatalogNotFound("Pipeline not found.")
        if status is not None and status not in {"running", "success", "partial", "failed", "cancelled"}:
            raise CatalogError("Unknown run status.")
        limit = max(1, min(limit, 100))
        offset = max(0, min(offset, 5_000))
        parameters: dict[str, Any] = {
            "pipeline": pipeline_id,
            "since": (self.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        status_clause = ""
        if status:
            parameters["status"] = status
            status_clause = "AND status = {status:String}"
        result = self._query(
            f"SELECT {RUN_COLUMNS} FROM meta.ingestion_runs FINAL "
            "WHERE pipeline = {pipeline:String} "
            "AND started_at >= parseDateTime64BestEffort({since:String}, 3, 'UTC') "
            f"{status_clause} ORDER BY started_at DESC LIMIT {limit + 1:d} OFFSET {offset:d}",
            parameters, query_settings("rows", max_rows=limit + 1),
        )
        runs = [_run_from_row(row) for row in _rows(result)]
        return PipelineRunsPage(
            pipeline=pipeline_id, items=runs[:limit], limit=limit, offset=offset, has_more=len(runs) > limit,
        )


def pipeline_status(
    pipeline: PipelineInfo,
    values: dict[str, Any],
    last_started: datetime | None,
    problems: list[PipelineRun],
    now: datetime,
) -> tuple[HubStatus, str]:
    """Judge a pipeline against its own schedule, not a fixed freshness rule."""

    if last_started is None:
        if not pipeline.scheduled:
            return "not_configured", "Not scheduled in production; it runs only when started by hand."
        if pipeline.expected_interval_seconds is None:
            return "not_expected", "No runs in the last 7 days; this pipeline runs only when there is work to do."
        if pipeline.session_calendar and not _session_open(pipeline.session_calendar, now):
            return "not_expected", "No runs in the last 7 days and the market is closed now."
        return "missing", "No runs were recorded in the last 7 days."
    age = (now - last_started).total_seconds()
    ago = f"{_duration(age)} ago"
    last_status = str(values.get("last_status") or "")
    interval = pipeline.expected_interval_seconds
    in_session = _session_open(pipeline.session_calendar, now)
    runs_24h = int(values.get("runs_24h") or 0)
    problem_24h = int(values.get("problem_24h") or 0)
    if last_status == "running" and interval and age > 2 * interval:
        return "attention", f"The latest run started {ago} and has not finished."
    if interval and in_session and age > interval:
        return "attention", f"No run for {_duration(age)}; it normally runs at least every {_duration(interval)}."
    if pipeline.per_symbol_runs:
        if runs_24h and problem_24h / runs_24h > 0.2:
            share = round(problem_24h * 100 / runs_24h)
            return "attention", f"{share}% of runs in the last 24 hours failed or were incomplete."
    elif last_status in PROBLEM_STATUSES:
        detail = problems[0].error if problems and problems[0].error else "no error text was recorded"
        label = "failed" if last_status == "failed" else "was incomplete"
        return "attention", f"The latest run {label} ({ago}): {detail[:160]}"
    if pipeline.session_calendar and not in_session:
        return "not_expected", f"Outside collection hours; the last run was {ago}."
    return "healthy", f"Last run {ago} ({last_status or 'recorded'})."
