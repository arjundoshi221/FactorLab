"""Read models and ClickHouse queries for the private FactorLab web hub."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from factorlab.api.india import QueryClient
from factorlab.api.india_observability import _session_state
from factorlab.api.schema_map import V2_DATABASES, V2_DOMAINS
from factorlab.storage.clickhouse import ClickHouseStorage

IST = ZoneInfo("Asia/Kolkata")
POLITICAL_FRESHNESS_SECONDS = 172_800
MARKET_FRESHNESS_SECONDS = 600

HubStatus = Literal[
    "healthy",
    "attention",
    "missing",
    "not_expected",
    "not_configured",
    "unknown",
]


class HubTable(BaseModel):
    name: str
    domain: str
    category: str
    engine: str
    kind: Literal["table", "view"] = "table"
    stored_rows: int
    bytes_on_disk: int
    count_kind: Literal["stored"] = "stored"
    first_data_at: str | None = None
    last_data_at: str | None = None
    last_ingested_at: datetime | None = None
    today_rows: int | None = None
    today_status: HubStatus
    status_reason: str


class HubDatabaseSummary(BaseModel):
    database: str
    table_count: int
    view_count: int = 0
    populated_tables: int
    stored_rows: int
    bytes_on_disk: int
    healthy_tables: int
    attention_tables: int


class HubIndiaSummary(BaseModel):
    trading_date: date
    market_status: str
    expected_series: int
    data_points: int
    expected_data_points: int
    coverage_percent: float
    last_ingested_at: datetime | None
    freshness_seconds: int | None
    status: HubStatus
    status_reason: str


class HubPoliticalSummary(BaseModel):
    checked_at: datetime
    datasets_with_data: int
    fresh_datasets: int
    stale_datasets: int
    last_ingested_at: datetime | None
    status: HubStatus
    status_reason: str


class HubBuild(BaseModel):
    release_id: str | None
    commit: str | None


def current_build() -> HubBuild:
    """Describe the image this API runs from, as baked in by the release build."""

    return HubBuild(
        release_id=os.getenv("FACTORLAB_RELEASE_ID") or None,
        commit=os.getenv("FACTORLAB_COMMIT") or None,
    )


class HubOverview(BaseModel):
    generated_at: datetime
    build: HubBuild | None = None
    summary: HubDatabaseSummary
    india: HubIndiaSummary
    political: HubPoliticalSummary
    tables: list[HubTable]
    warnings: list[str]


@dataclass(frozen=True)
class TableProfile:
    domain: str
    category: str
    first_expression: str
    last_expression: str
    ingested_expression: str
    today_expression: str
    today_timezone: str = "UTC"
    status_policy: Literal[
        "market_intraday", "political", "ingestion", "not_expected", "not_configured"
    ] = "not_expected"
    note: str | None = None
    final: bool = True


TABLE_PROFILES: dict[str, TableProfile] = {
    "raw_http_archive": TableProfile(
        "Raw archive", "Raw", "fetched_at", "fetched_at", "fetched_at", "fetched_at"
    ),
    "ref_countries": TableProfile(
        "Reference", "Reference", "ingested_at", "ingested_at", "ingested_at", "ingested_at"
    ),
    "ref_exchanges": TableProfile(
        "Reference", "Reference", "ingested_at", "ingested_at", "ingested_at", "ingested_at"
    ),
    "ref_instruments": TableProfile(
        "Market reference", "Reference", "first_seen", "last_seen", "ingested_at", "ingested_at"
    ),
    "ref_contracts": TableProfile(
        "Market reference", "Reference", "first_seen", "last_seen", "ingested_at", "ingested_at"
    ),
    "market_candles_1min": TableProfile(
        "Global market",
        "Market data",
        "bar_time",
        "bar_time",
        "ingested_at",
        "bar_time",
        "UTC",
        "not_expected",
    ),
    "market_candles_daily": TableProfile(
        "Global market",
        "Market data",
        "trade_date",
        "trade_date",
        "ingested_at",
        "trade_date",
        "UTC",
        "not_expected",
    ),
    "alt_political_legislators": TableProfile(
        "Political", "Reference", "term_start", "term_end", "ingested_at", "ingested_at"
    ),
    "alt_political_committees": TableProfile(
        "Political", "Reference", "ingested_at", "ingested_at", "ingested_at", "ingested_at"
    ),
    "alt_political_committee_memberships": TableProfile(
        "Political",
        "Political data",
        "snapshot_date",
        "snapshot_date",
        "ingested_at",
        "snapshot_date",
        "UTC",
        "political",
    ),
    "alt_political_house_filings": TableProfile(
        "Political",
        "Political data",
        "filing_date",
        "filing_date",
        "ingested_at",
        "filing_date",
        "UTC",
        "political",
    ),
    "alt_political_trades": TableProfile(
        "Political",
        "Political data",
        "transaction_date",
        "transaction_date",
        "ingested_at",
        "transaction_date",
        "UTC",
        "political",
    ),
    "india_expected_series": TableProfile(
        "India market",
        "Operations",
        "ingested_at",
        "ingested_at",
        "ingested_at",
        "ingested_at",
    ),
    "ingestion_runs": TableProfile(
        "Pipelines",
        "Operations",
        "started_at",
        "started_at",
        "ingested_at",
        "started_at",
        "UTC",
        "ingestion",
    ),
    **{name: TableProfile("US market", "Operations", timestamp, timestamp, timestamp, timestamp)
       for name, timestamp in [("us_expected_series", "ingested_at"), ("us_recovery_state", "ingested_at"),
                               ("us_session_coverage", "ingested_at"), ("us_source_status", "checked_at")]},
}

MARKET_PANEL_NOTE = "Collection health is evaluated per market in the India and US panels."
ARCHIVE_NOTE = "Frozen pre-cutover copy kept after the v2 table exchange; no writer."
MIGRATION_NOTE = "Migration control-plane record; written only by reviewed migration runs."
VIEW_NOTE = "Read-only research view over v2 tables; it stores no rows."
NO_PRODUCER_NOTE = "The v2 schema is ready; no producer writes this table yet."
DERIVED_NOTE = "No daily freshness rule; dates reflect stored rows."


def _v2(database: str, category: str, first: str, last: str, ingested: str, today: str, **options: Any) -> TableProfile:
    return TableProfile(V2_DOMAINS[database], category, first, last, ingested, today, **options)


V2_TABLE_PROFILES: dict[str, TableProfile] = {
    "raw.archive": _v2("raw", "Raw payloads", "fetched_at", "fetched_at", "fetched_at", "fetched_at", final=False),
    "ref.countries": _v2("ref", "Reference", "ingested_at", "ingested_at", "ingested_at", "ingested_at"),
    "ref.exchanges": _v2("ref", "Reference", "ingested_at", "ingested_at", "ingested_at", "ingested_at"),
    "ref.listings": _v2("ref", "Market reference", "ingested_at", "ingested_at", "ingested_at", "ingested_at"),
    "ref.contracts": _v2("ref", "Market reference", "expiry", "expiry", "ingested_at", "ingested_at"),
    "market.bars": _v2("market", "Equity bars", "bar_time", "bar_time", "ingested_at", "bar_time", note=MARKET_PANEL_NOTE),
    "market.futures_contract_bars": _v2(
        "market", "Futures contract bars", "bar_time", "bar_time", "ingested_at", "bar_time", note=MARKET_PANEL_NOTE
    ),
    "alt.political_committees": _v2("alt", "Political reference", "ingested_at", "ingested_at", "ingested_at", "ingested_at"),
    "alt.political_committee_memberships": _v2(
        "alt", "Political", "effective_from", "effective_from", "ingested_at", "effective_from", status_policy="political"
    ),
    "alt.political_filings": _v2(
        "alt", "Political", "filing_date", "filing_date", "ingested_at", "filing_date", status_policy="political"
    ),
    "alt.political_trades": _v2(
        "alt", "Political", "transaction_date", "transaction_date", "ingested_at", "transaction_date",
        status_policy="political",
    ),
    "meta.expected_series": _v2("meta", "Collection plan", "ingested_at", "ingested_at", "ingested_at", "ingested_at"),
    # Decades of daily rows per listing: physical rows keep the overview inside the deploy check's timeout.
    "meta.session_coverage": _v2(
        "meta", "Collection coverage", "trade_date", "trade_date", "ingested_at", "ingested_at", final=False
    ),
    "meta.recovery_state": _v2("meta", "History recovery", "ingested_at", "ingested_at", "ingested_at", "ingested_at"),
    "meta.source_status": _v2("meta", "Source status", "checked_at", "checked_at", "checked_at", "checked_at"),
    "meta.ingestion_runs": _v2(
        "meta", "Pipelines", "started_at", "started_at", "ingested_at", "started_at", status_policy="ingestion"
    ),
}

# Business-time columns in order of preference for a table's coverage dates.
DATA_TIME_COLUMNS = (
    "bar_time", "observed_at", "exec_time", "event_time", "snapshot_time", "trade_date", "transaction_date",
    "filing_date", "period_end", "event_date", "ex_date", "effective_date", "effective_from", "term_start",
    "holiday_date", "valid_from", "detected_at", "fetched_at", "produced_at", "started_at", "checked_at",
    "assigned_at", "computed_at", "as_of_time", "ingested_at", "updated_at",
)
INGESTED_TIME_COLUMNS = (
    "ingested_at", "fetched_at", "produced_at", "checked_at", "updated_at", "computed_at", "started_at",
)
MIGRATION_TABLES = frozenset({
    "meta.schema_migrations", "meta.migration_runs", "meta.migration_id_crosswalk",
    "meta.migration_reference_enrichment", "meta.migration_political_trade_enrichment",
})


def _default_profile(name: str, columns: list[str]) -> TableProfile | None:
    """Derive a date-only profile from a v2 table's columns when none is registered."""

    database = name.split(".", 1)[0]
    domain = V2_DOMAINS.get(database)
    available = set(columns)
    data_time = next((column for column in DATA_TIME_COLUMNS if column in available), None)
    ingested = next((column for column in INGESTED_TIME_COLUMNS if column in available), None)
    if domain is None or data_time is None or ingested is None:
        return None
    if name.endswith("_canonical"):
        category, note = "Pre-cutover archive", ARCHIVE_NOTE
    elif name in MIGRATION_TABLES:
        category, note = "Migration", MIGRATION_NOTE
    else:
        category, note = domain, DERIVED_NOTE
    # Physical counts keep the refresh inexpensive on tables without a health rule.
    return TableProfile(domain, category, data_time, data_time, ingested, ingested, note=note, final=False)


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _freshness_seconds(value: datetime | None, now: datetime) -> int | None:
    timestamp = _aware_utc(value)
    if timestamp is None:
        return None
    return max(int((now - timestamp).total_seconds()), 0)


class HubRepository:
    """Build a dashboard overview from FactorLab and ClickHouse metadata."""

    def __init__(self, client: QueryClient, *, database: str = "factorlab_v2") -> None:
        self.client = client
        self.database = database

    @classmethod
    def from_environment(cls) -> HubRepository:
        storage = ClickHouseStorage.from_environment()
        return cls(storage.client)

    def get_overview(self, *, now: datetime | None = None) -> HubOverview:
        checked_at = _aware_utc(now or datetime.now(UTC))
        assert checked_at is not None
        today_ist = checked_at.astimezone(IST).date()
        warnings: list[str] = []
        metadata = self._table_metadata()
        expected_series = self._active_expected_series(warnings)
        latest_run_failures = self._latest_run_failures(warnings)
        tables: list[HubTable] = []

        for row in metadata:
            name = str(row["name"])
            engine = str(row["engine"])
            database = name.split(".", 1)[0]
            is_view = engine in {"View", "MaterializedView"}
            profile = V2_TABLE_PROFILES.get(name)
            if profile is None and not is_view:
                profile = _default_profile(name, list(row.get("columns") or []))
            table = HubTable(
                name=name,
                domain=profile.domain if profile else V2_DOMAINS.get(database, "Unclassified"),
                category=profile.category if profile else ("View" if is_view else "Other"),
                engine=engine,
                kind="view" if is_view else "table",
                stored_rows=int(row["stored_rows"] or 0),
                bytes_on_disk=int(row["bytes_on_disk"] or 0),
                today_status="not_expected" if is_view else ("not_configured" if profile is None else "unknown"),
                status_reason=(
                    VIEW_NOTE
                    if is_view
                    else "This table has no recognizable time columns to evaluate."
                    if profile is None
                    else "Health has not been evaluated."
                ),
            )
            if profile is not None and table.stored_rows == 0 and profile.status_policy == "not_expected":
                # Nothing to aggregate; say whether any producer is expected to fill it.
                table.today_status, table.status_reason = (
                    ("not_expected", profile.note)
                    if profile.note in {ARCHIVE_NOTE, MIGRATION_NOTE, MARKET_PANEL_NOTE}
                    else ("not_configured", NO_PRODUCER_NOTE)
                )
            elif profile is not None:
                try:
                    aggregate = self._profile_aggregate(name, profile, today_ist)
                    table.first_data_at = _iso(aggregate["first_data_at"])
                    table.last_data_at = _iso(aggregate["last_data_at"])
                    table.last_ingested_at = _aware_utc(aggregate["last_ingested_at"])
                    table.today_rows = int(aggregate["today_rows"] or 0)
                    table.today_status, table.status_reason = self._table_status(
                        table,
                        profile,
                        checked_at,
                        today_ist,
                        expected_series,
                        latest_run_failures,
                    )
                except Exception as exc:  # noqa: BLE001 - one table must not blank the hub
                    table.today_status = "unknown"
                    table.status_reason = "This table could not be evaluated on the latest refresh."
                    warnings.append(f"{name}: {type(exc).__name__}")
            tables.append(table)

        india = self._india_summary(tables, checked_at, today_ist, expected_series)
        political = self._political_summary(tables, checked_at)
        attention = {"attention", "missing", "unknown"}
        stored = [item for item in tables if item.kind == "table"]
        summary = HubDatabaseSummary(
            database=self.database,
            table_count=len(stored),
            view_count=len(tables) - len(stored),
            populated_tables=sum(item.stored_rows > 0 for item in stored),
            stored_rows=sum(item.stored_rows for item in stored),
            bytes_on_disk=sum(item.bytes_on_disk for item in stored),
            healthy_tables=sum(item.today_status == "healthy" for item in stored),
            attention_tables=sum(item.today_status in attention for item in stored),
        )
        return HubOverview(
            generated_at=checked_at,
            build=current_build(),
            summary=summary,
            india=india,
            political=political,
            tables=sorted(tables, key=lambda item: (item.domain, item.name)),
            warnings=warnings,
        )

    def _table_metadata(self) -> list[dict[str, Any]]:
        result = self.client.query(
            """
            WITH parts AS (
                SELECT database, table, sum(rows) AS stored_rows, sum(bytes_on_disk) AS bytes_on_disk
                FROM system.parts
                WHERE active AND has({databases:Array(String)}, database)
                GROUP BY database, table
            ), table_columns AS (
                SELECT database, table, groupArray(name) AS columns
                FROM system.columns
                WHERE has({databases:Array(String)}, database)
                GROUP BY database, table
            )
            SELECT concat(tables.database, '.', tables.name) AS name, tables.engine,
                   ifNull(parts.stored_rows, 0) AS stored_rows,
                   ifNull(parts.bytes_on_disk, 0) AS bytes_on_disk,
                   table_columns.columns AS columns
            FROM system.tables AS tables
            LEFT JOIN parts ON parts.database = tables.database AND parts.table = tables.name
            LEFT JOIN table_columns
                ON table_columns.database = tables.database AND table_columns.table = tables.name
            WHERE has({databases:Array(String)}, tables.database)
            ORDER BY tables.database, tables.name
            """,
            parameters={"databases": list(V2_DATABASES)},
        )
        return _rows(result)

    def _profile_aggregate(
        self, table_name: str, profile: TableProfile, today_ist: date, *, market_code: str | None = None
    ) -> dict[str, Any]:
        timezone = profile.today_timezone
        today_expression = (
            f"toDate({profile.today_expression}, '{timezone}')"
            if timezone != "UTC"
            else f"toDate({profile.today_expression})"
        )
        result = self.client.query(
            f"""
            SELECT minOrNull({profile.first_expression}) AS first_data_at,
                   maxOrNull({profile.last_expression}) AS last_data_at,
                   maxOrNull({profile.ingested_expression}) AS last_ingested_at,
                   countIf({today_expression} = {{today:Date}}) AS today_rows
            FROM {table_name}{' FINAL' if profile.final else ''}
            {"WHERE country_code = {market_code:String}" if market_code else ""}
            """,
            parameters={"today": today_ist, **({"market_code": market_code} if market_code else {})},
        )
        return _rows(result)[0]

    def _active_expected_series(self, warnings: list[str]) -> int:
        try:
            result = self.client.query(
                "SELECT count() AS expected_series FROM meta.expected_series FINAL WHERE country_code = 'IN' AND active"
            )
            return int(_rows(result)[0]["expected_series"])
        except Exception as exc:  # noqa: BLE001 - degraded overview is still useful
            warnings.append(f"meta.expected_series health: {type(exc).__name__}")
            return 0

    def _latest_run_failures(self, warnings: list[str]) -> int:
        try:
            result = self.client.query(
                """
                SELECT countIf(latest_status IN ('failed', 'partial')) AS failed_pipelines
                FROM (
                    SELECT country_code, pipeline, source, argMax(status, started_at) AS latest_status
                    FROM meta.ingestion_runs FINAL
                    GROUP BY country_code, pipeline, source
                )
                """
            )
            return int(_rows(result)[0]["failed_pipelines"])
        except Exception as exc:  # noqa: BLE001 - degraded overview is still useful
            warnings.append(f"ingestion run health: {type(exc).__name__}")
            return 0

    def _table_status(
        self,
        table: HubTable,
        profile: TableProfile,
        now: datetime,
        trading_date: date,
        expected_series: int,
        latest_run_failures: int,
    ) -> tuple[HubStatus, str]:
        if profile.status_policy == "not_configured":
            return "not_configured", "No active producer is configured for this table yet."
        if profile.status_policy == "not_expected":
            return "not_expected", profile.note or "This table does not have a daily row-count expectation."
        if profile.status_policy == "political":
            if table.stored_rows == 0:
                return "missing", "This dataset has never been populated."
            age = _freshness_seconds(table.last_ingested_at, now)
            if age is None or age > POLITICAL_FRESHNESS_SECONDS:
                return "attention", "The latest ingestion is older than the 48-hour freshness window."
            return "healthy", "The dataset is within its 48-hour freshness window."
        if profile.status_policy == "ingestion":
            if table.stored_rows == 0:
                return "missing", "No ingestion runs have been recorded."
            if latest_run_failures:
                return "attention", f"{latest_run_failures} pipeline(s) most recently failed or were partial."
            return "healthy", "All recorded pipelines have a non-failing latest outcome."
        if profile.status_policy == "market_intraday":
            market_status, points_per_series = _session_state(trading_date, now)
            if market_status in {"pre_open", "non_trading_day"}:
                label = "before market open" if market_status == "pre_open" else "a non-trading day"
                return "not_expected", f"Today is {label}; intraday rows are not expected yet."
            expected = expected_series * points_per_series
            actual = int(table.today_rows or 0)
            if expected == 0:
                return "not_configured", "No active India series are configured."
            if actual == 0:
                return "missing", f"No intraday points exist; {expected:,} are expected so far."
            coverage = min(actual * 100.0 / expected, 100.0)
            freshness = _freshness_seconds(table.last_ingested_at, now)
            target = 95.0 if market_status == "open" else 99.0
            if coverage >= target and (market_status == "closed" or (freshness or 0) <= MARKET_FRESHNESS_SECONDS):
                return "healthy", f"{coverage:.1f}% of expected intraday points are present."
            return "attention", f"{coverage:.1f}% coverage; the {target:.0f}% target is not currently met."
        return "unknown", "No health rule is available."

    def _india_summary(
        self,
        tables: list[HubTable],
        now: datetime,
        trading_date: date,
        expected_series: int,
    ) -> HubIndiaSummary:
        market_status, points_per_series = _session_state(trading_date, now)
        table = next((item for item in tables if item.name == "market.bars"), None)
        if table:
            table = table.model_copy(deep=True)
            profile = replace(V2_TABLE_PROFILES["market.bars"],
                              today_timezone="Asia/Kolkata", status_policy="market_intraday")
            try:
                aggregate = self._profile_aggregate("market.bars", profile, trading_date, market_code="IN")
                future = self._profile_aggregate("market.futures_contract_bars", profile, trading_date, market_code="IN")
                table.today_rows = int(aggregate["today_rows"] or 0) + int(future["today_rows"] or 0)
                table.last_ingested_at = max(
                    (
                        value
                        for value in (
                            _aware_utc(aggregate["last_ingested_at"]),
                            _aware_utc(future["last_ingested_at"]),
                        )
                        if value is not None
                    ),
                    default=None,
                )
                table.today_status, table.status_reason = self._table_status(
                    table, profile, now, trading_date, expected_series, 0)
            except Exception:  # noqa: BLE001 - keep the overview available when a table is unavailable
                table.today_rows = None
                table.last_ingested_at = None
                table.today_status = "unknown"
                table.status_reason = "India collection could not be evaluated."
        actual = int(table.today_rows or 0) if table else 0
        expected = expected_series * points_per_series
        coverage = 100.0 if expected == 0 else min(round(actual * 100.0 / expected, 2), 100.0)
        status: HubStatus = table.today_status if table else "unknown"
        reason = table.status_reason if table else "The intraday table is not present."
        last_ingested = table.last_ingested_at if table else None
        return HubIndiaSummary(
            trading_date=trading_date,
            market_status=market_status,
            expected_series=expected_series,
            data_points=actual,
            expected_data_points=expected,
            coverage_percent=coverage,
            last_ingested_at=last_ingested,
            freshness_seconds=_freshness_seconds(last_ingested, now),
            status=status,
            status_reason=reason,
        )

    def _political_summary(
        self, tables: list[HubTable], now: datetime
    ) -> HubPoliticalSummary:
        datasets = [
            item
            for item in tables
            if V2_TABLE_PROFILES.get(item.name)
            and V2_TABLE_PROFILES[item.name].status_policy == "political"
        ]
        latest = max(
            (item.last_ingested_at for item in datasets if item.last_ingested_at is not None),
            default=None,
        )
        stale = sum(item.today_status in {"attention", "missing", "unknown"} for item in datasets)
        fresh = sum(item.today_status == "healthy" for item in datasets)
        if not datasets:
            status: HubStatus = "unknown"
            reason = "No political datasets are registered."
        elif stale:
            status = "attention"
            reason = f"{stale} political dataset(s) need attention."
        else:
            status = "healthy"
            reason = "All populated political datasets are within their freshness window."
        return HubPoliticalSummary(
            checked_at=now,
            datasets_with_data=sum(item.stored_rows > 0 for item in datasets),
            fresh_datasets=fresh,
            stale_datasets=stale,
            last_ingested_at=latest,
            status=status,
            status_reason=reason,
        )


class HubOverviewService:
    """Thread-safe short-lived cache for the auto-refreshing dashboard."""

    def __init__(
        self,
        repository: HubRepository,
        *,
        ttl_seconds: float = 55.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: HubOverview | None = None
        self._expires_at = 0.0

    def get_overview(self) -> HubOverview:
        now = self.clock()
        if self._cached is not None and now < self._expires_at:
            return self._cached
        with self._lock:
            now = self.clock()
            if self._cached is not None and now < self._expires_at:
                return self._cached
            overview = self.repository.get_overview()
            self._cached = overview
            self._expires_at = now + self.ttl_seconds
            return overview
