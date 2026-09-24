"""Operational read models and ClickHouse queries for Indian data collection."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
from fastapi import HTTPException, status
from pydantic import BaseModel

from factorlab.api.india import (
    INDIA_BAR_VERSIONS_SQL,
    INDIA_BARS_SQL,
    IndiaInstrument,
    QueryClient,
)
from factorlab.storage.clickhouse import ClickHouseStorage

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
FULL_SESSION_POINTS = 375
NO_CONTRACT_ID = UUID(int=0)


class IndiaDashboard(BaseModel):
    trading_date: date
    market_status: Literal["pre_open", "open", "closed", "non_trading_day"]
    reference_instruments: int
    expected_series: int
    series_with_data: int
    data_points: int
    expected_data_points: int
    coverage_percent: float
    collected_on_date: int
    backfilled_on_date: int
    last_bar_time: datetime | None
    last_ingested_at: datetime | None
    freshness_seconds: int | None
    anomaly_count: int


class IndiaCoverageItem(BaseModel):
    listing_id: UUID
    contract_id: UUID | None
    symbol: str
    source: str
    universe: str
    data_points: int
    expected_data_points: int
    coverage_percent: float
    first_bar_time: datetime | None
    last_bar_time: datetime | None
    last_ingested_at: datetime | None
    missing_points: int
    freshness_seconds: int | None
    status: Literal["complete", "partial", "missing"]


class IndiaCoveragePage(BaseModel):
    trading_date: date
    items: list[IndiaCoverageItem]
    limit: int
    offset: int


class CollectionActivitySummary(BaseModel):
    ingestion_date: date
    rows_collected: int
    unique_instruments: int
    unique_series: int
    current_market_date_rows: int
    backfilled_rows: int
    earliest_market_date: date | None
    latest_market_date: date | None
    first_ingested_at: datetime | None
    last_ingested_at: datetime | None


class CollectionActivityBucket(BaseModel):
    bucket: str
    data_points: int
    unique_series: int


class IndiaCollectionActivity(BaseModel):
    summary: CollectionActivitySummary
    by_hour: list[CollectionActivityBucket]
    by_source: list[CollectionActivityBucket]


class IndiaFreshnessItem(BaseModel):
    listing_id: UUID
    contract_id: UUID | None
    symbol: str
    source: str
    last_bar_time: datetime | None
    last_ingested_at: datetime | None
    freshness_seconds: int | None
    status: Literal["live", "stale", "never_seen"]


class IndiaFreshnessPage(BaseModel):
    checked_at: datetime
    stale_after_seconds: int
    items: list[IndiaFreshnessItem]
    limit: int
    offset: int


class IndiaGap(BaseModel):
    listing_id: UUID
    contract_id: UUID | None
    symbol: str
    source: str
    gap_start: datetime
    gap_end: datetime
    missing_points: int
    severity: Literal["warning", "critical"]


class IndiaGapsPage(BaseModel):
    trading_date: date
    items: list[IndiaGap]
    limit: int
    offset: int


class IndiaAnomaly(BaseModel):
    anomaly_id: str
    trading_date: date
    anomaly_type: str
    severity: Literal["warning", "critical"]
    listing_id: UUID
    contract_id: UUID | None
    symbol: str
    source: str
    observed_value: str
    expected_value: str | None
    explanation: str
    detected_at: datetime


class IndiaAnomaliesPage(BaseModel):
    trading_date: date
    items: list[IndiaAnomaly]
    limit: int
    offset: int


class IndiaMetricPoint(BaseModel):
    bucket: datetime
    value: float


class IndiaMetricsSeries(BaseModel):
    metric: str
    group_by: str
    points: list[IndiaMetricPoint]


class IndiaContractSummary(BaseModel):
    contract_id: UUID | None
    underlying_listing_id: UUID
    contract_type: str
    expiry: date
    active: bool


class IndiaInstrumentDataSummary(BaseModel):
    data_points: int
    trading_days: int
    unique_series: int
    first_bar_time: datetime | None
    last_bar_time: datetime | None
    last_ingested_at: datetime | None
    missing_points_today: int
    anomaly_count_today: int


class IndiaInstrumentSummary(BaseModel):
    instrument: IndiaInstrument
    contracts: list[IndiaContractSummary]
    data: IndiaInstrumentDataSummary


class IndiaIngestionRun(BaseModel):
    run_id: UUID
    pipeline: str
    source: str
    universe: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    requested_series: int
    successful_series: int
    failed_series: int
    rows_written: int
    error: str | None
    metadata_json: str


class IndiaIngestionRunsPage(BaseModel):
    items: list[IndiaIngestionRun]
    limit: int
    offset: int


class IndiaSourceStatus(BaseModel):
    source: str
    pipeline: str
    run_status: str
    last_run_started_at: datetime
    last_run_completed_at: datetime | None
    last_success_at: datetime | None
    last_bar_time: datetime | None
    last_ingested_at: datetime | None
    freshness_seconds: int | None
    status: Literal["healthy", "delayed", "failed", "running"]


class IndiaSourceStatusPage(BaseModel):
    checked_at: datetime
    items: list[IndiaSourceStatus]


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _session_state(trading_date: date, now: datetime) -> tuple[str, int]:
    calendar = xcals.get_calendar("XBOM")
    if not calendar.is_session(pd.Timestamp(trading_date)):
        return "non_trading_day", 0
    local_now = now.astimezone(IST)
    if trading_date < local_now.date():
        return "closed", FULL_SESSION_POINTS
    if trading_date > local_now.date() or local_now.time() < SESSION_OPEN:
        return "pre_open", 0
    if local_now.time() >= SESSION_CLOSE:
        return "closed", FULL_SESSION_POINTS
    opened = datetime.combine(trading_date, SESSION_OPEN, tzinfo=IST)
    return "open", min(int((local_now - opened).total_seconds() // 60) + 1, FULL_SESSION_POINTS)


class IndiaObservabilityRepository:
    """Read operational coverage and ingestion state from ClickHouse."""

    def __init__(self, client: QueryClient) -> None:
        self.client = client

    def _query(self, sql: str, parameters: dict[str, Any] | None = None):
        sql = sql.replace("__INDIA_BARS_FINAL__", f"({INDIA_BARS_SQL}) AS bars")
        sql = sql.replace("__INDIA_BARS_ALL__", f"({INDIA_BAR_VERSIONS_SQL}) AS bars")
        return self.client.query(sql, parameters=parameters)

    @classmethod
    def from_environment(cls) -> IndiaObservabilityRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def get_dashboard(self, *, trading_date: date, now: datetime | None = None) -> IndiaDashboard:
        checked_at = now or datetime.now(UTC)
        market_status, points_per_series = _session_state(trading_date, checked_at)
        reference_result = self._query(
            """
            SELECT
                (SELECT uniqExact(listing_id) FROM ref.listings FINAL
                 WHERE country_code = 'IN') AS reference_instruments,
                (SELECT count() FROM meta.expected_series FINAL
                 WHERE country_code = 'IN' AND active) AS expected_series
            """
        )
        market_result = self._query(
            """
            SELECT uniqExact(tuple(listing_id, contract_id)) AS series_with_data,
                   count() AS data_points,
                   maxOrNull(bar_time) AS last_bar_time,
                   maxOrNull(ingested_at) AS last_ingested_at
            FROM __INDIA_BARS_FINAL__
            WHERE country_code = 'IN'
              AND trade_date = {trading_date:Date}
            """,
            parameters={"trading_date": trading_date},
        )
        collection_result = self._query(
            """
            SELECT count() AS collected_on_date,
                   countIf(trade_date < {trading_date:Date})
                       AS backfilled_on_date
            FROM __INDIA_BARS_ALL__
            WHERE country_code = 'IN'
              AND toDate(ingested_at, 'Asia/Kolkata') = {trading_date:Date}
            """,
            parameters={"trading_date": trading_date},
        )
        row = {
            **_rows(reference_result)[0],
            **_rows(market_result)[0],
            **_rows(collection_result)[0],
        }
        expected = int(row["expected_series"]) * points_per_series
        actual = int(row["data_points"])
        freshness = _age_seconds(row["last_ingested_at"], checked_at)
        anomaly_count = len(
            self.list_anomalies(
                trading_date=trading_date,
                limit=1_000_000,
                now=checked_at,
            ).items
        )
        return IndiaDashboard(
            trading_date=trading_date,
            market_status=market_status,
            expected_data_points=expected,
            coverage_percent=_percent(actual, expected),
            freshness_seconds=freshness,
            anomaly_count=anomaly_count,
            **row,
        )

    def list_coverage(
        self,
        *,
        trading_date: date,
        symbol: str | None = None,
        source: str | None = None,
        status_filter: str | None = None,
        min_coverage: float | None = None,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> IndiaCoveragePage:
        checked_at = now or datetime.now(UTC)
        _, expected_points = _session_state(trading_date, checked_at)
        conditions = ["expected.country_code = 'IN'", "expected.active"]
        parameters: dict[str, Any] = {
            "trading_date": trading_date,
            "expected_points": expected_points,
        }
        if symbol:
            conditions.append("expected.symbol = {symbol:String}")
            parameters["symbol"] = symbol.upper()
        if source:
            conditions.append("expected.source = {source:String}")
            parameters["source"] = source
        result = self._query(
            f"""
            WITH actual AS (
                SELECT listing_id, contract_id, source, count() AS data_points,
                       min(bar_time) AS first_bar_time, max(bar_time) AS last_bar_time,
                       max(ingested_at) AS last_ingested_at
                FROM __INDIA_BARS_FINAL__
                WHERE country_code = 'IN'
                  AND toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
                GROUP BY listing_id, contract_id, source
            )
            SELECT expected.listing_id, expected.contract_id, expected.symbol,
                   expected.source, expected.universe,
                   ifNull(actual.data_points, 0) AS data_points,
                   {{expected_points:UInt16}} AS expected_data_points,
                   actual.first_bar_time, actual.last_bar_time, actual.last_ingested_at
            FROM meta.expected_series AS expected FINAL
            LEFT JOIN actual ON expected.listing_id = actual.listing_id AND ifNull(expected.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) = ifNull(actual.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) AND expected.source = actual.source
            WHERE {" AND ".join(conditions)}
            ORDER BY expected.symbol, expected.contract_id
            SETTINGS join_use_nulls = 1
            """,
            parameters=parameters,
        )
        items = []
        for row in _rows(result):
            actual = int(row["data_points"])
            coverage = _percent(actual, expected_points)
            item_status = "missing" if actual == 0 else "complete" if coverage >= 100 else "partial"
            if status_filter and item_status != status_filter:
                continue
            if min_coverage is not None and coverage < min_coverage:
                continue
            items.append(
                IndiaCoverageItem(
                    **row,
                    coverage_percent=coverage,
                    missing_points=max(expected_points - actual, 0),
                    freshness_seconds=_age_seconds(row["last_ingested_at"], checked_at),
                    status=item_status,
                )
            )
        return IndiaCoveragePage(
            trading_date=trading_date,
            items=items[offset : offset + limit],
            limit=limit,
            offset=offset,
        )

    def get_collection_activity(self, *, ingestion_date: date) -> IndiaCollectionActivity:
        parameters = {"ingestion_date": ingestion_date}
        summary = self._query(
            """
            SELECT count() AS rows_collected,
                   uniqExact(listing_id) AS unique_instruments,
                   uniqExact(tuple(listing_id, contract_id)) AS unique_series,
                   countIf(trade_date = {ingestion_date:Date})
                       AS current_market_date_rows,
                   countIf(trade_date < {ingestion_date:Date})
                       AS backfilled_rows,
                   minOrNull(trade_date) AS earliest_market_date,
                   maxOrNull(trade_date) AS latest_market_date,
                   minOrNull(ingested_at) AS first_ingested_at,
                   maxOrNull(ingested_at) AS last_ingested_at
            FROM __INDIA_BARS_ALL__
            WHERE country_code = 'IN'
              AND toDate(ingested_at, 'Asia/Kolkata') = {ingestion_date:Date}
            """,
            parameters=parameters,
        )
        hourly = self._query(
            """
            SELECT formatDateTime(toStartOfHour(ingested_at, 'Asia/Kolkata'), '%H:00') AS bucket,
                   count() AS data_points,
                   uniqExact(tuple(listing_id, contract_id)) AS unique_series
            FROM __INDIA_BARS_ALL__
            WHERE country_code = 'IN'
              AND toDate(ingested_at, 'Asia/Kolkata') = {ingestion_date:Date}
            GROUP BY bucket ORDER BY bucket
            """,
            parameters=parameters,
        )
        sources = self._query(
            """
            SELECT source AS bucket, count() AS data_points,
                   uniqExact(tuple(listing_id, contract_id)) AS unique_series
            FROM __INDIA_BARS_ALL__
            WHERE country_code = 'IN'
              AND toDate(ingested_at, 'Asia/Kolkata') = {ingestion_date:Date}
            GROUP BY source ORDER BY source
            """,
            parameters=parameters,
        )
        summary_row = _rows(summary)[0]
        return IndiaCollectionActivity(
            summary=CollectionActivitySummary(ingestion_date=ingestion_date, **summary_row),
            by_hour=[CollectionActivityBucket.model_validate(row) for row in _rows(hourly)],
            by_source=[CollectionActivityBucket.model_validate(row) for row in _rows(sources)],
        )

    def list_freshness(
        self,
        *,
        stale_after_seconds: int,
        source: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> IndiaFreshnessPage:
        checked_at = now or datetime.now(UTC)
        conditions = ["expected.country_code = 'IN'", "expected.active"]
        parameters: dict[str, Any] = {}
        if source:
            conditions.append("expected.source = {source:String}")
            parameters["source"] = source
        result = self._query(
            f"""
            WITH latest AS (
                SELECT listing_id, contract_id, source, max(bar_time) AS last_bar_time,
                       max(ingested_at) AS last_ingested_at
                FROM __INDIA_BARS_FINAL__ WHERE country_code = 'IN'
                GROUP BY listing_id, contract_id, source
            )
            SELECT expected.listing_id AS listing_id,
                   expected.contract_id AS contract_id,
                   expected.symbol AS symbol,
                   expected.source AS source,
                   latest.last_bar_time, latest.last_ingested_at
            FROM meta.expected_series AS expected FINAL
            LEFT JOIN latest ON expected.listing_id = latest.listing_id AND ifNull(expected.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) = ifNull(latest.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) AND expected.source = latest.source
            WHERE {" AND ".join(conditions)}
            ORDER BY latest.last_ingested_at ASC NULLS FIRST, expected.symbol
            SETTINGS join_use_nulls = 1
            """,
            parameters=parameters,
        )
        items = []
        for row in _rows(result):
            age = _age_seconds(row["last_ingested_at"], checked_at)
            item_status = (
                "never_seen" if age is None else "stale" if age > stale_after_seconds else "live"
            )
            if not status_filter or item_status == status_filter:
                items.append(IndiaFreshnessItem(**row, freshness_seconds=age, status=item_status))
        return IndiaFreshnessPage(
            checked_at=checked_at,
            stale_after_seconds=stale_after_seconds,
            items=items[offset : offset + limit],
            limit=limit,
            offset=offset,
        )

    def list_gaps(
        self,
        *,
        trading_date: date,
        symbol: str | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> IndiaGapsPage:
        _, expected_points = _session_state(trading_date, now or datetime.now(UTC))
        if expected_points == 0:
            return IndiaGapsPage(trading_date=trading_date, items=[], limit=limit, offset=offset)
        conditions = ["country_code = 'IN'", "active"]
        parameters: dict[str, Any] = {
            "trading_date": trading_date,
            "expected_points": expected_points,
            "limit": limit,
            "offset": offset,
        }
        if symbol:
            conditions.append("symbol = {symbol:String}")
            parameters["symbol"] = symbol.upper()
        if source:
            conditions.append("source = {source:String}")
            parameters["source"] = source
        result = self._query(
            f"""
            WITH expected AS (
                SELECT listing_id, contract_id, symbol, source,
                       addMinutes(toDateTime(concat(toString({{trading_date:Date}}),
                                  ' 09:15:00'), 'Asia/Kolkata'), minute) AS expected_time
                FROM meta.expected_series FINAL
                CROSS JOIN (SELECT number AS minute FROM numbers({{expected_points:UInt16}})) AS minutes
                WHERE {" AND ".join(conditions)}
            ), actual AS (
                SELECT listing_id, contract_id, source, bar_time
                FROM __INDIA_BARS_FINAL__
                WHERE country_code = 'IN'
                  AND toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
            ), missing AS (
                SELECT expected.* FROM expected
                LEFT JOIN actual ON expected.listing_id = actual.listing_id
                    AND ifNull(expected.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) = ifNull(actual.contract_id, toUUID('00000000-0000-0000-0000-000000000000'))
                    AND expected.source = actual.source
                    AND expected.expected_time = actual.bar_time
                WHERE actual.bar_time IS NULL
            ), grouped AS (
                SELECT *, dateDiff('minute',
                           toDateTime(concat(toString({{trading_date:Date}}), ' 09:15:00'),
                                      'Asia/Kolkata'), expected_time)
                           - row_number() OVER (
                               PARTITION BY listing_id, contract_id, source
                               ORDER BY expected_time) AS gap_group
                FROM missing
            )
            SELECT listing_id, contract_id, any(symbol) AS symbol, source,
                   min(expected_time) AS gap_start, max(expected_time) AS gap_end,
                   count() AS missing_points
            FROM grouped
            GROUP BY listing_id, contract_id, source, gap_group
            ORDER BY missing_points DESC, symbol
            LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            SETTINGS join_use_nulls = 1
            """,
            parameters=parameters,
        )
        items = [
            IndiaGap(**row, severity="critical" if int(row["missing_points"]) >= 15 else "warning")
            for row in _rows(result)
        ]
        return IndiaGapsPage(trading_date=trading_date, items=items, limit=limit, offset=offset)

    def list_anomalies(
        self,
        *,
        trading_date: date,
        severity: str | None = None,
        symbol: str | None = None,
        source: str | None = None,
        stale_after_seconds: int = 300,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> IndiaAnomaliesPage:
        checked_at = now or datetime.now(UTC)
        market_status, expected_points = _session_state(trading_date, checked_at)
        conditions = ["expected.country_code = 'IN'", "expected.active"]
        parameters: dict[str, Any] = {"trading_date": trading_date}
        if symbol:
            conditions.append("expected.symbol = {symbol:String}")
            parameters["symbol"] = symbol.upper()
        if source:
            conditions.append("expected.source = {source:String}")
            parameters["source"] = source
        result = self._query(
            f"""
            WITH latest AS (
                SELECT listing_id, contract_id, source, count() AS data_points,
                       uniqExact(close) AS distinct_closes,
                       countIf(low > high OR open < low OR open > high OR close < low OR close > high)
                           AS ohlc_violations,
                       countIf(open < 0 OR high < 0 OR low < 0 OR close < 0
                               OR volume < 0 OR oi < 0) AS negative_values,
                       countIf(
                           toHour(toTimeZone(bar_time, 'Asia/Kolkata')) * 60
                               + toMinute(toTimeZone(bar_time, 'Asia/Kolkata')) < 555
                           OR toHour(toTimeZone(bar_time, 'Asia/Kolkata')) * 60
                               + toMinute(toTimeZone(bar_time, 'Asia/Kolkata')) >= 930)
                           AS outside_session,
                       if(minIf(low, low > 0) > 0,
                          max(high) / minIf(low, low > 0) - 1, 0) AS price_range_ratio,
                       if(avg(volume) > 0, max(volume) / avg(volume), 0) AS volume_spike_ratio,
                       max(bar_time) AS last_bar_time, max(ingested_at) AS last_ingested_at
                FROM __INDIA_BARS_FINAL__
                WHERE country_code = 'IN'
                  AND toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
                GROUP BY listing_id, contract_id, source
            ), versions AS (
                SELECT listing_id, contract_id, source,
                       count() - uniqExact(tuple(bar_time, source)) AS duplicate_versions
                FROM __INDIA_BARS_ALL__
                WHERE country_code = 'IN'
                  AND toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
                GROUP BY listing_id, contract_id, source
            )
            SELECT expected.listing_id AS listing_id,
                   expected.contract_id AS contract_id,
                   expected.symbol AS symbol,
                   expected.source AS source,
                   ifNull(latest.data_points, 0) AS data_points,
                   ifNull(latest.distinct_closes, 0) AS distinct_closes,
                   ifNull(latest.ohlc_violations, 0) AS ohlc_violations,
                   ifNull(latest.negative_values, 0) AS negative_values,
                   ifNull(latest.outside_session, 0) AS outside_session,
                   ifNull(latest.price_range_ratio, 0) AS price_range_ratio,
                   ifNull(latest.volume_spike_ratio, 0) AS volume_spike_ratio,
                   ifNull(versions.duplicate_versions, 0) AS duplicate_versions,
                   latest.last_ingested_at AS last_ingested_at
            FROM meta.expected_series AS expected FINAL
            LEFT JOIN latest ON expected.listing_id = latest.listing_id AND ifNull(expected.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) = ifNull(latest.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) AND expected.source = latest.source
            LEFT JOIN versions ON expected.listing_id = versions.listing_id AND ifNull(expected.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) = ifNull(versions.contract_id, toUUID('00000000-0000-0000-0000-000000000000')) AND expected.source = versions.source
            WHERE {" AND ".join(conditions)}
            ORDER BY expected.symbol, expected.contract_id
            SETTINGS join_use_nulls = 1
            """,
            parameters=parameters,
        )
        anomalies: list[IndiaAnomaly] = []
        for row in _rows(result):
            anomalies.extend(
                _anomalies_for_row(
                    row,
                    trading_date=trading_date,
                    expected_points=expected_points,
                    checked_at=checked_at,
                    check_staleness=market_status == "open",
                    stale_after_seconds=stale_after_seconds,
                )
            )
        if severity:
            anomalies = [item for item in anomalies if item.severity == severity]
        return IndiaAnomaliesPage(
            trading_date=trading_date,
            items=anomalies[offset : offset + limit],
            limit=limit,
            offset=offset,
        )

    def get_metrics(
        self,
        *,
        metric: str,
        group_by: str,
        date_from: date,
        date_to: date,
        source: str | None = None,
    ) -> IndiaMetricsSeries:
        bucket = {
            "hour": "toStartOfHour(bar_time, 'Asia/Kolkata')",
            "day": "toStartOfDay(bar_time, 'Asia/Kolkata')",
        }[group_by]
        expected_bucket_points = (
            str(FULL_SESSION_POINTS)
            if group_by == "day"
            else "multiIf(toHour(toTimeZone(min(bar_time), 'Asia/Kolkata')) = 9, 45, "
            "toHour(toTimeZone(min(bar_time), 'Asia/Kolkata')) = 15, 30, 60)"
        )
        expression = {
            "data_points": "count()",
            "unique_instruments": "uniqExact(listing_id)",
            "unique_series": "uniqExact(tuple(listing_id, contract_id))",
            "ingestion_lag": "avg(dateDiff('second', bar_time, ingested_at))",
            "missing_points": (
                "greatest((SELECT count() FROM meta.expected_series FINAL WHERE country_code = 'IN' AND active) "
                f"* ({expected_bucket_points}) - count(), 0)"
            ),
            "coverage_percent": (
                "if((SELECT count() FROM meta.expected_series FINAL WHERE country_code = 'IN' AND active) = 0, 100, "
                f"least(count() * 100.0 / ((SELECT count() FROM meta.expected_series FINAL "
                f"WHERE country_code = 'IN' AND active) * ({expected_bucket_points})), 100))"
            ),
            "anomaly_count": (
                "countIf(low > high OR open < low OR open > high OR close < low OR close > high "
                "OR open < 0 OR high < 0 OR low < 0 OR close < 0)"
            ),
        }[metric]
        conditions = [
            "country_code = 'IN'",
            "toDate(bar_time, 'Asia/Kolkata') >= {date_from:Date}",
            "toDate(bar_time, 'Asia/Kolkata') <= {date_to:Date}",
        ]
        parameters: dict[str, Any] = {"date_from": date_from, "date_to": date_to}
        if source:
            conditions.append("source = {source:String}")
            parameters["source"] = source
        result = self._query(
            f"""
            SELECT {bucket} AS bucket, toFloat64({expression}) AS value
            FROM __INDIA_BARS_FINAL__
            WHERE {" AND ".join(conditions)}
            GROUP BY bucket ORDER BY bucket
            """,
            parameters=parameters,
        )
        return IndiaMetricsSeries(
            metric=metric,
            group_by=group_by,
            points=[IndiaMetricPoint.model_validate(row) for row in _rows(result)],
        )

    def get_instrument_summary(
        self,
        listing_id: UUID,
        *,
        now: datetime | None = None,
    ) -> IndiaInstrumentSummary:
        parameters = {"listing_id": listing_id}
        instrument_result = self._query(
            """
            SELECT l.listing_id, s.security_id AS security_id, l.trading_symbol,
                   e.legal_name AS name, s.isin, l.exchange_code,
                   s.security_type, s.currency_code, l.lot_size, l.tick_size,
                   if(l.active, 'active', 'inactive') AS status,
                   ifNull(a.source, 'reference') AS source,
                   e.first_seen, e.last_seen, l.ingested_at AS ingested_at
            FROM ref.listings AS l FINAL
            INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
            INNER JOIN ref.entities AS e FINAL ON e.entity_id = s.entity_id
            LEFT JOIN (
                SELECT target_id, argMax(source, version) AS source
                FROM ref.identifier_aliases FINAL
                WHERE target_kind = 'listing' AND alias_kind = 'upstox_instrument_key'
                GROUP BY target_id
            ) AS a ON a.target_id = l.listing_id
            WHERE l.listing_id = {listing_id:UUID} AND l.country_code = 'IN' LIMIT 1
            """,
            parameters=parameters,
        )
        instrument_rows = _rows(instrument_result)
        if not instrument_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found"
            )
        contracts_result = self._query(
            """
            SELECT contract_id, underlying_listing_id, contract_type, expiry, active
            FROM ref.contracts FINAL
            WHERE underlying_listing_id = {listing_id:UUID}
            ORDER BY expiry DESC, contract_id
            """,
            parameters=parameters,
        )
        data_result = self._query(
            f"""
            SELECT count() AS data_points,
                   uniqExact(trade_date) AS trading_days,
                   uniqExact(tuple(listing_id, contract_id)) AS unique_series,
                   minOrNull(bar_time) AS first_bar_time, maxOrNull(bar_time) AS last_bar_time,
                   maxOrNull(ingested_at) AS last_ingested_at
            FROM ({INDIA_BARS_SQL}) AS bars WHERE listing_id = {{listing_id:UUID}}
            """,
            parameters=parameters,
        )
        data = _rows(data_result)[0]
        today = (now or datetime.now(UTC)).astimezone(IST).date()
        expected_result = self._query(
            f"""
            SELECT count() AS expected_series,
                   (SELECT count() FROM ({INDIA_BARS_SQL}) AS bars
                    WHERE listing_id = {{listing_id:UUID}}
                      AND trade_date = {{today:Date}}) AS today_points
            FROM meta.expected_series FINAL
            WHERE country_code = 'IN' AND listing_id = {{listing_id:UUID}} AND active
            """,
            parameters={**parameters, "today": today},
        )
        expected_row = _rows(expected_result)[0]
        _, points = _session_state(today, now or datetime.now(UTC))
        missing = max(
            int(expected_row["expected_series"]) * points - int(expected_row["today_points"]), 0
        )
        return IndiaInstrumentSummary(
            instrument=IndiaInstrument.model_validate(instrument_rows[0]),
            contracts=[IndiaContractSummary.model_validate(row) for row in _rows(contracts_result)],
            data=IndiaInstrumentDataSummary(
                **data,
                missing_points_today=missing,
                anomaly_count_today=int(missing > 0),
            ),
        )

    def list_ingestion_runs(
        self,
        *,
        pipeline: str | None = None,
        source: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> IndiaIngestionRunsPage:
        conditions = ["country_code = 'IN'"]
        parameters: dict[str, Any] = {"limit": limit, "offset": offset}
        for value, field in [(pipeline, "pipeline"), (source, "source"), (status_filter, "status")]:
            if value:
                conditions.append(f"{field} = {{{field}:String}}")
                parameters[field] = value
        result = self._query(
            f"""
            SELECT run_id, pipeline, source, universe_id AS universe,
                   status, started_at, completed_at,
                   requested_series, successful_series, failed_series, rows_written,
                   error, metadata_json
            FROM meta.ingestion_runs FINAL WHERE {" AND ".join(conditions)}
            ORDER BY started_at DESC, run_id DESC
            LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            """,
            parameters=parameters,
        )
        return IndiaIngestionRunsPage(
            items=[IndiaIngestionRun.model_validate(row) for row in _rows(result)],
            limit=limit,
            offset=offset,
        )

    def get_ingestion_run(self, run_id: UUID) -> IndiaIngestionRun:
        result = self._query(
            """
            SELECT run_id, pipeline, source, universe_id AS universe,
                   status, started_at, completed_at,
                   requested_series, successful_series, failed_series, rows_written,
                   error, metadata_json
            FROM meta.ingestion_runs FINAL
            WHERE country_code = 'IN' AND run_id = {run_id:UUID} LIMIT 1
            """,
            parameters={"run_id": run_id},
        )
        rows = _rows(result)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion run not found"
            )
        return IndiaIngestionRun.model_validate(rows[0])

    def list_source_status(self, *, stale_after_seconds: int = 600) -> IndiaSourceStatusPage:
        checked_at = datetime.now(UTC)
        result = self._query(
            """
            WITH candles AS (
                SELECT source, max(bar_time) AS last_bar_time,
                       max(ingested_at) AS last_ingested_at
                FROM __INDIA_BARS_FINAL__ WHERE country_code = 'IN' GROUP BY source
            ), runs AS (
                SELECT source, pipeline, argMax(status, started_at) AS run_status,
                       max(started_at) AS last_run_started_at,
                       argMax(completed_at, started_at) AS last_run_completed_at,
                       maxIf(completed_at, status = 'success') AS last_success_at
                FROM meta.ingestion_runs FINAL WHERE country_code = 'IN'
                GROUP BY source, pipeline
            )
            SELECT runs.source, runs.pipeline, runs.run_status, runs.last_run_started_at,
                   runs.last_run_completed_at, runs.last_success_at,
                   candles.last_bar_time, candles.last_ingested_at
            FROM runs LEFT JOIN candles USING (source)
            ORDER BY runs.source, runs.pipeline
            """
        )
        items = []
        for row in _rows(result):
            freshness = _age_seconds(row["last_ingested_at"], checked_at)
            run_status = row["run_status"]
            overall = (
                "running"
                if run_status == "running"
                else "failed"
                if run_status in {"failed", "partial"}
                else "delayed"
                if freshness is None or freshness > stale_after_seconds
                else "healthy"
            )
            items.append(IndiaSourceStatus(**row, freshness_seconds=freshness, status=overall))
        return IndiaSourceStatusPage(checked_at=checked_at, items=items)


def _percent(actual: int, expected: int) -> float:
    return 100.0 if expected == 0 else round(min(actual * 100.0 / expected, 100.0), 2)


def _age_seconds(timestamp: datetime | None, now: datetime) -> int | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max(int((now - timestamp.astimezone(UTC)).total_seconds()), 0)


def _anomalies_for_row(
    row: dict[str, Any],
    *,
    trading_date: date,
    expected_points: int,
    checked_at: datetime,
    check_staleness: bool,
    stale_after_seconds: int,
) -> list[IndiaAnomaly]:
    definitions: list[tuple[str, str, str, str | None, str]] = []
    actual = int(row["data_points"])
    if expected_points and actual == 0:
        definitions.append(
            (
                "missing_series",
                "critical",
                str(actual),
                str(expected_points),
                "No candles were collected for an expected series.",
            )
        )
    elif expected_points and actual < expected_points:
        missing = expected_points - actual
        definitions.append(
            (
                "missing_candles",
                "critical" if missing >= 15 else "warning",
                str(missing),
                "0",
                "Expected one-minute candles are missing.",
            )
        )
    checks = [
        (
            "ohlc_violation",
            "critical",
            int(row["ohlc_violations"]),
            "Candles violate OHLC price bounds.",
        ),
        (
            "negative_value",
            "critical",
            int(row["negative_values"]),
            "Negative price, volume, or open-interest values were found.",
        ),
        (
            "outside_session",
            "warning",
            int(row["outside_session"]),
            "Candles fall outside the regular NSE session.",
        ),
    ]
    for anomaly_type, severity, value, explanation in checks:
        if value:
            definitions.append((anomaly_type, severity, str(value), "0", explanation))
    duplicate_versions = int(row["duplicate_versions"])
    duplicate_threshold = max(actual * 20, 1000)
    if duplicate_versions > duplicate_threshold:
        definitions.append(
            (
                "duplicate_version_burst",
                "warning",
                str(duplicate_versions),
                f"<={duplicate_threshold}",
                "Stored replacement versions substantially exceed the normal polling pattern.",
            )
        )
    if actual >= 30 and int(row["distinct_closes"]) <= 1:
        definitions.append(
            (
                "flatline",
                "warning",
                str(row["distinct_closes"]),
                ">1",
                "Closing prices are flat across the session.",
            )
        )
    if float(row["price_range_ratio"]) > 0.5:
        definitions.append(
            (
                "extreme_price_move",
                "warning",
                f"{float(row['price_range_ratio']):.4f}",
                "<=0.5",
                "The intraday high-low range exceeds 50%.",
            )
        )
    if float(row["volume_spike_ratio"]) > 20:
        definitions.append(
            (
                "volume_spike",
                "warning",
                f"{float(row['volume_spike_ratio']):.2f}",
                "<=20",
                "Peak minute volume exceeds twenty times the session average.",
            )
        )
    age = _age_seconds(row["last_ingested_at"], checked_at)
    if check_staleness and age is not None and age > stale_after_seconds:
        definitions.append(
            (
                "stale_series",
                "critical",
                str(age),
                f"<={stale_after_seconds}",
                "The series has not been refreshed within the freshness threshold.",
            )
        )
    items = []
    for anomaly_type, severity, observed, expected, explanation in definitions:
        identity = "|".join(
            [
                trading_date.isoformat(),
                anomaly_type,
                str(row["listing_id"]),
                str(row["contract_id"]),
                str(row["source"]),
            ]
        )
        items.append(
            IndiaAnomaly(
                anomaly_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                trading_date=trading_date,
                anomaly_type=anomaly_type,
                severity=severity,
                listing_id=row["listing_id"],
                contract_id=row["contract_id"],
                symbol=row["symbol"],
                source=row["source"],
                observed_value=observed,
                expected_value=expected,
                explanation=explanation,
                detected_at=checked_at,
            )
        )
    return items
