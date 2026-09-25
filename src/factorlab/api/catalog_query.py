"""Validated, bounded SQL for the data catalog's row previews.

The hub is reachable without login, so every value here is treated as hostile:
table and column names must exist in the live catalog, filter values are bound
parameters, every query carries read-only resource limits, and payload columns of
``raw.archive`` never reach SQL.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

TABLE_NAME = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MAX_FILTERS = 8
MAX_IN_VALUES = 50
MAX_FILTER_VALUE_LENGTH = 200
MAX_LIMIT = 200
MAX_OFFSET = 5_000
DEFAULT_LIMIT = 50
CSV_MAX_ROWS = 10_000
STATS_SAMPLE_ROWS = 100_000
# Tables at or below this many stored rows are previewed whole; larger ones need a time window.
WINDOW_THRESHOLD_ROWS = 200_000
JSON_CELL_CHARS = 500
CSV_CELL_CHARS = 4_096

TypeClass = Literal["text", "enum", "number", "decimal", "integer", "datetime", "date", "uuid", "bool", "complex"]
QueryKind = Literal["rows", "csv", "stats", "activity"]

# raw.archive holds full vendor HTTP responses. Only these metadata columns are ever selected;
# a column added later stays hidden until it is reviewed and listed here.
RAW_ARCHIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("raw_id", "`raw_id`"),
    ("source", "`source`"),
    ("source_channel", "`source_channel`"),
    ("transport", "`transport`"),
    ("country_code", "`country_code`"),
    ("source_url", "cutQueryStringAndFragment(`source_url`)"),
    ("request_key", "`request_key`"),
    ("status_code", "`status_code`"),
    ("content_type", "`content_type`"),
    ("response_sha256", "`response_sha256`"),
    ("fetched_at", "`fetched_at`"),
    ("event_count", "`event_count`"),
    ("as_of_time", "`as_of_time`"),
)
# Columns hidden from previews in addition to the raw.archive allowlist.
HIDDEN_COLUMNS: dict[str, frozenset[str]] = {
    "meta.ingestion_runs": frozenset({"metadata_json"}),
}

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(access_token|refresh_token|id_token|token|api_key|apikey|client_secret|secret"
            r"|password|code|sig|signature)=[^&\s\"']+"
        ),
        r"\1=[redacted]",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (
        re.compile(
            r"(?i)(\"(?:access_token|refresh_token|id_token|token|api_key|client_secret|password)\"\s*:\s*\")[^\"]*\""
        ),
        r'\1[redacted]"',
    ),
)

OPERATORS: dict[str, frozenset[str]] = {
    "text": frozenset({"eq", "neq", "contains", "prefix", "in", "null", "notnull"}),
    "enum": frozenset({"eq", "neq", "contains", "prefix", "in", "null", "notnull"}),
    "integer": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "null", "notnull"}),
    "number": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "null", "notnull"}),
    "decimal": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "null", "notnull"}),
    "datetime": frozenset({"gte", "lt", "null", "notnull"}),
    "date": frozenset({"eq", "gte", "lt", "null", "notnull"}),
    "uuid": frozenset({"eq", "neq", "null", "notnull"}),
    "bool": frozenset({"eq", "null", "notnull"}),
    "complex": frozenset({"null", "notnull"}),
}
_COMPARATORS = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_CLICKHOUSE_LIMIT_CODES = {"158", "159", "160", "241", "307", "396"}


class CatalogError(Exception):
    """An error whose message is safe to show to catalog users."""

    status_code = 400


class CatalogNotFound(CatalogError):
    status_code = 404


class CatalogForbidden(CatalogError):
    status_code = 403


class CatalogBusy(CatalogError):
    status_code = 429


class CatalogTooExpensive(CatalogError):
    status_code = 422


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str

    @property
    def type_class(self) -> TypeClass:
        return type_class(self.type)


@dataclass(frozen=True)
class TableInfo:
    """What the query builder needs to know about one live table."""

    name: str
    engine: str
    columns: tuple[ColumnInfo, ...]
    stored_rows: int = 0
    time_column: str | None = None
    last_data_at: datetime | None = None

    @property
    def is_view(self) -> bool:
        return self.engine in {"View", "MaterializedView", "LiveView"}

    @property
    def uses_final(self) -> bool:
        return "Replacing" in self.engine or "Collapsing" in self.engine

    @property
    def windowed(self) -> bool:
        return self.time_column is not None and self.stored_rows > WINDOW_THRESHOLD_ROWS

    def column(self, name: str) -> ColumnInfo | None:
        return next((column for column in self.columns if column.name == name), None)


@dataclass(frozen=True)
class Filter:
    column: str
    operator: str
    value: str


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RowsRequest:
    filters: tuple[Filter, ...] = ()
    sort: str | None = None
    descending: bool = True
    limit: int = DEFAULT_LIMIT
    offset: int = 0
    start: datetime | None = None
    end: datetime | None = None


@dataclass
class BuiltQuery:
    sql: str
    parameters: dict[str, Any]
    settings: dict[str, Any]
    columns: list[str]
    window: Window | None
    final_applied: bool
    notes: list[str] = field(default_factory=list)


def type_class(clickhouse_type: str) -> TypeClass:
    """Classify a ClickHouse type after removing Nullable/LowCardinality wrappers."""

    inner = clickhouse_type.strip()
    while True:
        match = re.fullmatch(r"(?:Nullable|LowCardinality)\((.*)\)", inner)
        if match is None:
            break
        inner = match.group(1).strip()
    if inner.startswith(("String", "FixedString")):
        return "text"
    if inner.startswith("Enum"):
        return "enum"
    if re.match(r"U?Int\d+$", inner):
        return "integer"
    if inner.startswith("Float"):
        return "number"
    if inner.startswith("Decimal"):
        return "decimal"
    if inner.startswith("DateTime"):
        return "datetime"
    if inner.startswith("Date"):
        return "date"
    if inner == "UUID":
        return "uuid"
    if inner == "Bool":
        return "bool"
    return "complex"


def friendly_type(clickhouse_type: str) -> str:
    return {
        "text": "Text", "enum": "Category", "integer": "Whole number", "number": "Number",
        "decimal": "Decimal number", "datetime": "Timestamp (UTC)", "date": "Date", "uuid": "ID",
        "bool": "Yes / no",
    }.get(type_class(clickhouse_type), "List" if "Array(" in clickhouse_type else "Structured value")


def validate_table_name(name: str) -> str:
    if not TABLE_NAME.fullmatch(name):
        raise CatalogNotFound("Table not found.")
    return name


def env_flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in {"0", "off", "false", "no", "disabled"}


def preview_denied(table: str) -> bool:
    """Return whether FACTORLAB_CATALOG_PREVIEW_DENY blocks rows for ``table``."""

    patterns = [item.strip() for item in os.getenv("FACTORLAB_CATALOG_PREVIEW_DENY", "").split(",") if item.strip()]
    for pattern in patterns:
        if pattern.endswith(".*") and table.startswith(pattern[:-1]):
            return True
        if pattern == table:
            return True
    return False


def csv_max_rows() -> int:
    try:
        configured = int(os.getenv("FACTORLAB_CATALOG_CSV_MAX_ROWS", str(CSV_MAX_ROWS)))
    except ValueError:
        configured = CSV_MAX_ROWS
    return max(1, min(configured, CSV_MAX_ROWS))


def ensure_preview_allowed(table: TableInfo, *, csv: bool = False) -> None:
    if not env_flag("FACTORLAB_CATALOG_PREVIEW"):
        raise CatalogForbidden("Row previews are disabled on this hub.")
    if csv and not env_flag("FACTORLAB_CATALOG_CSV"):
        raise CatalogForbidden("CSV downloads are disabled on this hub.")
    if table.is_view:
        raise CatalogForbidden("Views describe queries over other tables; preview the underlying tables instead.")
    if preview_denied(table.name):
        raise CatalogForbidden("Row previews are disabled for this table.")


def visible_columns(table: TableInfo) -> list[tuple[str, str]]:
    """Return ``(name, select expression)`` for every column a preview may show."""

    if table.name == "raw.archive":
        present = {column.name for column in table.columns}
        return [(name, expression) for name, expression in RAW_ARCHIVE_COLUMNS if name in present]
    hidden = HIDDEN_COLUMNS.get(table.name, frozenset())
    return [(column.name, f"`{column.name}`") for column in table.columns if column.name not in hidden]


def hidden_columns(table: TableInfo) -> list[str]:
    shown = {name for name, _ in visible_columns(table)}
    return [column.name for column in table.columns if column.name not in shown]


def _visible_column(table: TableInfo, name: str, purpose: str) -> ColumnInfo:
    if not COLUMN_NAME.fullmatch(name):
        raise CatalogError(f"Unknown column for {purpose}.")
    column = table.column(name)
    if column is None:
        raise CatalogError(f"Unknown column for {purpose}: {name}.")
    if name not in {visible for visible, _ in visible_columns(table)}:
        raise CatalogError(f"Column {name} is hidden and cannot be used for {purpose}.")
    return column


def parse_filters(items: Iterable[tuple[str, str]]) -> tuple[Filter, ...]:
    """Parse ``f.<column>=<operator>:<value>`` query items, splitting on the first colon."""

    filters: list[Filter] = []
    for key, raw in items:
        if not key.startswith("f."):
            continue
        column = key[2:]
        operator, separator, value = raw.partition(":")
        if not separator and operator not in {"null", "notnull"}:
            raise CatalogError("Filters must look like f.column=operator:value.")
        filters.append(Filter(column=column, operator=operator, value=value))
        if len(filters) > MAX_FILTERS:
            raise CatalogError(f"At most {MAX_FILTERS} filters are allowed.")
    return tuple(filters)


def parse_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise CatalogError(f"{label} must be an ISO date or timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def default_window_width(table: str) -> timedelta:
    if table.startswith(("market.", "raw.")):
        return timedelta(days=1)
    if table == "meta.session_coverage":
        return timedelta(days=31)
    return timedelta(days=30)


def max_window_width(table: str) -> timedelta:
    return timedelta(days=31) if table.startswith(("market.", "raw.")) else timedelta(days=366)


def resolve_window(table: TableInfo, start: datetime | None, end: datetime | None, *, now: datetime) -> Window | None:
    if not table.windowed:
        # Small tables are previewed whole, but an explicit range still narrows them.
        if table.time_column is None or (start is None and end is None):
            return None
        start = start or datetime(1900, 1, 1, tzinfo=UTC)
        end = end or now + timedelta(days=3_660)
        if start >= end:
            raise CatalogError("The window start must be before its end.")
        return Window(start=start, end=end)
    anchor = table.last_data_at or now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    width = default_window_width(table.name)
    if end is None:
        # Include the anchor's last second so the newest rows fall inside [start, end).
        end = (start + width) if start is not None else anchor + timedelta(seconds=1)
    if start is None:
        start = end - width
    if start >= end:
        raise CatalogError("The window start must be before its end.")
    if end - start > max_window_width(table.name):
        days = max_window_width(table.name).days
        raise CatalogError(f"Choose a window of at most {days} days for this table.")
    return Window(start=start, end=end)


def query_settings(kind: QueryKind, *, max_rows: int) -> dict[str, Any]:
    """Resource limits and read-only mode applied to every catalog query."""

    heavy = kind in {"activity", "stats"}
    return {
        "readonly": 2,
        "max_execution_time": 20 if heavy else 15,
        "max_result_rows": max_rows,
        "result_overflow_mode": "break",
        "max_result_bytes": 25_000_000 if kind == "csv" else 8_000_000,
        "max_rows_to_read": 200_000_000 if kind == "activity" else 50_000_000,
        "max_bytes_to_read": 4_000_000_000,
        "read_overflow_mode": "throw",
        "max_memory_usage": 1_000_000_000,
        "log_comment": f"hub-catalog:{kind}",
    }


class _Parameters:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def add(self, value: Any, clickhouse_type: str) -> str:
        name = f"p{len(self.values)}"
        self.values[name] = value
        return f"{{{name}:{clickhouse_type}}}"


def _filter_sql(table: TableInfo, item: Filter, parameters: _Parameters) -> str:
    column = _visible_column(table, item.column, "filtering")
    kind = column.type_class
    if item.operator not in OPERATORS[kind]:
        allowed = ", ".join(sorted(OPERATORS[kind]))
        raise CatalogError(f"Operator {item.operator!r} is not available for {column.name}; use one of {allowed}.")
    reference = f"`{column.name}`"
    if item.operator == "null":
        return f"isNull({reference})"
    if item.operator == "notnull":
        return f"isNotNull({reference})"
    value = item.value.strip()
    if len(value) > MAX_FILTER_VALUE_LENGTH:
        raise CatalogError("Filter values are limited to 200 characters.")
    if kind in {"text", "enum"}:
        subject = f"toString({reference})" if kind == "enum" else reference
        if item.operator == "contains":
            return f"positionCaseInsensitive(toString({reference}), {parameters.add(value, 'String')}) > 0"
        if item.operator == "prefix":
            return f"startsWith(toString({reference}), {parameters.add(value, 'String')})"
        if item.operator == "in":
            values = [part.strip() for part in value.split(",") if part.strip()]
            if not values or len(values) > MAX_IN_VALUES:
                raise CatalogError(f"The 'in' operator takes 1 to {MAX_IN_VALUES} comma-separated values.")
            return f"{subject} IN {parameters.add(values, 'Array(String)')}"
        return f"{subject} {_COMPARATORS[item.operator]} {parameters.add(value, 'String')}"
    if kind == "integer":
        try:
            number: Any = int(value)
        except ValueError as exc:
            raise CatalogError(f"{column.name} needs a whole number.") from exc
        return f"{reference} {_COMPARATORS[item.operator]} {parameters.add(number, 'Int64')}"
    if kind in {"number", "decimal"}:
        try:
            number = float(value)
        except ValueError as exc:
            raise CatalogError(f"{column.name} needs a number.") from exc
        if not math.isfinite(number):
            raise CatalogError(f"{column.name} needs a finite number.")
        subject = f"toFloat64({reference})" if kind == "decimal" else reference
        return f"{subject} {_COMPARATORS[item.operator]} {parameters.add(number, 'Float64')}"
    if kind == "datetime":
        moment = _timestamp_text(parse_timestamp(value, column.name))
        return (
            f"{reference} {_COMPARATORS[item.operator]} "
            f"parseDateTime64BestEffort({parameters.add(moment, 'String')}, 3, 'UTC')"
        )
    if kind == "date":
        try:
            day = date.fromisoformat(value)
        except ValueError as exc:
            raise CatalogError(f"{column.name} needs a date like 2026-09-25.") from exc
        return f"{reference} {_COMPARATORS[item.operator]} toDate({parameters.add(day.isoformat(), 'String')})"
    if kind == "uuid":
        try:
            identifier = str(UUID(value))
        except ValueError as exc:
            raise CatalogError(f"{column.name} needs a UUID.") from exc
        return f"{reference} {_COMPARATORS[item.operator]} toUUID({parameters.add(identifier, 'String')})"
    if kind == "bool":
        truth = value.lower()
        if truth not in {"true", "false", "1", "0", "yes", "no"}:
            raise CatalogError(f"{column.name} needs true or false.")
        return f"{reference} = {parameters.add(1 if truth in {'true', '1', 'yes'} else 0, 'UInt8')}"
    raise CatalogError(f"{column.name} cannot be filtered by value.")


def _where(table: TableInfo, window: Window | None, filters: Iterable[Filter], parameters: _Parameters) -> str:
    clauses: list[str] = []
    if window is not None and table.time_column:
        reference = f"`{table.time_column}`"
        start = parameters.add(_timestamp_text(window.start), "String")
        end = parameters.add(_timestamp_text(window.end), "String")
        clauses.append(f"{reference} >= parseDateTime64BestEffort({start}, 3, 'UTC')")
        clauses.append(f"{reference} < parseDateTime64BestEffort({end}, 3, 'UTC')")
    clauses.extend(_filter_sql(table, item, parameters) for item in filters)
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def build_rows_query(
    table: TableInfo, request: RowsRequest, *, now: datetime, kind: Literal["rows", "csv"] = "rows",
) -> BuiltQuery:
    """Build the bounded SELECT for a preview page or CSV export."""

    if kind == "rows":
        limit = max(1, min(request.limit, MAX_LIMIT))
        offset = max(0, min(request.offset, MAX_OFFSET))
        fetch = limit + 1
    else:
        limit, offset = csv_max_rows(), 0
        fetch = limit
    window = resolve_window(table, request.start, request.end, now=now)
    parameters = _Parameters()
    where = _where(table, window, request.filters, parameters)
    columns = visible_columns(table)
    sort = request.sort or table.time_column
    order = ""
    if sort:
        _visible_column(table, sort, "sorting")
        order = f"ORDER BY `{sort}` {'DESC' if request.descending else 'ASC'}"
    final = table.uses_final
    select = ", ".join(
        expression if expression == f"`{name}`" else f"{expression} AS `{name}`" for name, expression in columns
    )
    sql = (
        f"SELECT {select} FROM {table.name}{' FINAL' if final else ''} {where} {order} "
        f"LIMIT {fetch:d} OFFSET {offset:d}"
    )
    notes: list[str] = []
    if table.name == "raw.archive":
        notes.append("Payload bodies, response headers, and metadata are hidden; URLs omit query strings.")
    if final:
        notes.append("Shows the latest version of each row.")
    return BuiltQuery(
        sql=re.sub(r"\s+", " ", sql).strip(),
        parameters=parameters.values,
        settings=query_settings(kind, max_rows=fetch),
        columns=[name for name, _ in columns],
        window=window,
        final_applied=final,
        notes=notes,
    )


def build_stats_query(table: TableInfo, *, now: datetime) -> BuiltQuery:
    """Summarize each visible column over the most recent sample of stored rows."""

    window = resolve_window(table, None, None, now=now)
    parameters = _Parameters()
    where = _where(table, window, (), parameters)
    columns = visible_columns(table)
    order = f"ORDER BY `{table.time_column}` DESC" if table.time_column else ""
    inner = ", ".join(
        expression if expression == f"`{name}`" else f"{expression} AS `{name}`" for name, expression in columns
    )
    aggregates = ["count() AS `__rows`"]
    for name, _ in columns:
        column = table.column(name)
        assert column is not None
        kind = column.type_class
        reference = f"`{name}`"
        aggregates.append(f"countIf(isNull({reference})) AS `{name}__nulls`")
        if kind == "complex":
            continue
        aggregates.append(f"uniq({reference}) AS `{name}__distinct`")
        if kind in {"integer", "number", "decimal", "datetime", "date", "bool"}:
            aggregates.append(f"toString(min({reference})) AS `{name}__min`")
            aggregates.append(f"toString(max({reference})) AS `{name}__max`")
        elif kind in {"text", "enum"}:
            aggregates.append(f"topK(5)(substring(toString({reference}), 1, 64)) AS `{name}__top`")
    sql = (
        f"SELECT {', '.join(aggregates)} FROM (SELECT {inner} FROM {table.name} {where} {order} "
        f"LIMIT {STATS_SAMPLE_ROWS:d})"
    )
    return BuiltQuery(
        sql=re.sub(r"\s+", " ", sql).strip(),
        parameters=parameters.values,
        settings=query_settings("stats", max_rows=1),
        columns=[name for name, _ in columns],
        window=window,
        final_applied=False,
    )


def build_activity_query(table: TableInfo, grain: Literal["day", "month"], *, now: datetime) -> BuiltQuery:
    if not table.time_column:
        raise CatalogError("This table has no time column to chart.")
    anchor = table.last_data_at or now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    span = timedelta(days=90) if grain == "day" else timedelta(days=366 * 3)
    window = Window(start=anchor - span, end=anchor + timedelta(seconds=1))
    parameters = _Parameters()
    where = _where(table, window, (), parameters)
    bucket = "toStartOfDay" if grain == "day" else "toStartOfMonth"
    sql = (
        f"SELECT toDate({bucket}(`{table.time_column}`)) AS bucket, count() AS rows "
        f"FROM {table.name} {where} GROUP BY bucket ORDER BY bucket"
    )
    return BuiltQuery(
        sql=sql,
        parameters=parameters.values,
        settings=query_settings("activity", max_rows=2_000),
        columns=["bucket", "rows"],
        window=window,
        final_applied=False,
    )


def strip_query_string(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value.split("?", 1)[0]
    if parts.scheme or parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return value.split("?", 1)[0]


def scrub_text(value: str, *, limit: int) -> tuple[str, bool]:
    """Redact credential-looking fragments and truncate long cells."""

    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    if len(value) > limit:
        return value[: limit - 1] + "…", True
    return value, False


def cell_value(value: Any, *, column: str, table: str, limit: int) -> tuple[Any, bool]:
    """Convert one ClickHouse value to a JSON/CSV-safe value; returns (value, truncated)."""

    if value is None or isinstance(value, bool):
        return value, False
    if isinstance(value, int):
        return (value if abs(value) <= 2**53 else str(value)), False
    if isinstance(value, float):
        return (value if math.isfinite(value) else str(value)), False
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.isoformat(), False
    if isinstance(value, (date, UUID, Decimal)):
        return str(value), False
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, (list, tuple, dict, set)):
        text = json.dumps(list(value) if isinstance(value, set) else value, default=str, ensure_ascii=False)
    else:
        text = str(value)
    if table == "raw.archive" and column in {"source_url", "request_key"}:
        text = strip_query_string(text)
    return scrub_text(text, limit=limit)


def clean_row(row: Iterable[Any], columns: list[str], *, table: str, limit: int) -> tuple[list[Any], int]:
    values: list[Any] = []
    truncated = 0
    for column, value in zip(columns, row, strict=False):
        cleaned, cut = cell_value(value, column=column, table=table, limit=limit)
        values.append(cleaned)
        truncated += int(cut)
    return values, truncated


def translate_database_error(exc: Exception) -> CatalogError:
    """Map ClickHouse failures to safe messages without echoing server text."""

    message = str(exc)
    code = re.search(r"Code:\s*(\d+)", message)
    if code and code.group(1) in _CLICKHOUSE_LIMIT_CODES:
        return CatalogTooExpensive("This query is too large. Narrow the date range or add a filter.")
    error = CatalogError("The data store could not run this preview. Try again shortly.")
    error.status_code = 502
    return error


class QuerySlots:
    """Cap concurrent preview queries so a public hub cannot saturate ClickHouse."""

    def __init__(self, slots: int = 3) -> None:
        self._semaphore = threading.BoundedSemaphore(slots)

    def acquire(self) -> None:
        if not self._semaphore.acquire(blocking=False):
            raise CatalogBusy("The hub is busy running other previews. Try again in a few seconds.")

    def release(self) -> None:
        self._semaphore.release()


def describe_request(request: RowsRequest, window: Window | None) -> Mapping[str, str]:
    """Summarize a CSV export for response headers."""

    summary = {
        "X-FactorLab-Filters": "; ".join(f"{item.column} {item.operator} {item.value}" for item in request.filters)
        or "none",
        "X-FactorLab-Row-Cap": str(csv_max_rows()),
    }
    if window is not None:
        summary["X-FactorLab-Window"] = f"{window.start.isoformat()}/{window.end.isoformat()}"
    return {key: value.encode("ascii", "replace").decode("ascii")[:500] for key, value in summary.items()}
