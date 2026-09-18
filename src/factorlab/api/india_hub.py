"""Read models for the private India Markets web explorer."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import exchange_calendars as xcals
import pandas as pd
from fastapi import HTTPException, status
from pydantic import BaseModel

from factorlab.api.india import QueryClient
from factorlab.api.india_observability import _session_state
from factorlab.storage.clickhouse import ClickHouseStorage

IndiaHubStatus = Literal["healthy", "attention", "missing", "not_expected"]
IndiaCollectionScope = Literal["all", "collecting", "historical", "not_configured"]
IndiaCollectionStatus = Literal["collecting", "historical", "not_configured"]


class IndiaHubInstrument(BaseModel):
    instrument_id: UUID
    symbol: str
    name: str | None
    exchange_code: str | None
    segment: str | None
    instrument_type: str | None
    status: str | None
    source: str | None
    collection_status: IndiaCollectionStatus
    data_points: int
    trading_days: int
    unique_series: int
    first_bar_time: datetime | None
    last_bar_time: datetime | None
    last_ingested_at: datetime | None
    expected_series: int
    selected_data_points: int
    selected_unique_series: int
    expected_data_points: int
    coverage_percent: float | None
    ohlc_violations: int
    null_ohlc_values: int
    outside_session: int
    duplicate_versions: int
    quality_issue_count: int
    check_status: IndiaHubStatus
    check_reason: str


class IndiaHubInstrumentPage(BaseModel):
    trading_date: date
    market_status: str
    expected_points_per_series: int
    scope: IndiaCollectionScope
    items: list[IndiaHubInstrument]
    total: int
    reference_total: int
    collecting_total: int
    historical_total: int
    not_configured_total: int
    limit: int
    offset: int
    data_as_of: datetime | None


class IndiaHubInstrumentDay(BaseModel):
    trading_date: date
    market_status: str
    data_points: int
    unique_series: int
    expected_series: int
    expected_data_points: int
    coverage_percent: float | None
    first_bar_time: datetime | None
    last_bar_time: datetime | None
    last_ingested_at: datetime | None
    ohlc_violations: int
    null_ohlc_values: int
    outside_session: int
    duplicate_versions: int
    quality_issue_count: int
    check_status: IndiaHubStatus
    check_reason: str


class IndiaHubInstrumentDays(BaseModel):
    instrument_id: UUID
    date_from: date
    date_to: date
    items: list[IndiaHubInstrumentDay]


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _quality_result(
    *,
    market_status: str,
    data_points: int,
    expected_series: int,
    expected_points_per_series: int,
    ohlc_violations: int,
    null_ohlc_values: int,
    outside_session: int,
    duplicate_versions: int,
) -> tuple[int, float | None, int, IndiaHubStatus, str]:
    expected_data_points = expected_series * expected_points_per_series
    coverage = (
        None
        if expected_data_points == 0
        else round(min(data_points * 100.0 / expected_data_points, 100.0), 2)
    )
    duplicate_burst = duplicate_versions > max(data_points * 20, 1000)
    quality_issues = ohlc_violations + null_ohlc_values + outside_session + int(duplicate_burst)

    if quality_issues:
        details = []
        if ohlc_violations:
            details.append(f"{ohlc_violations} OHLC violation(s)")
        if null_ohlc_values:
            details.append(f"{null_ohlc_values} candle(s) with missing OHLC")
        if outside_session:
            details.append(f"{outside_session} outside-session candle(s)")
        if duplicate_burst:
            details.append("unusually high replacement-version volume")
        return expected_data_points, coverage, quality_issues, "attention", "; ".join(details)

    if market_status == "non_trading_day":
        if data_points:
            return (
                expected_data_points,
                coverage,
                quality_issues,
                "attention",
                "Candles exist on a non-trading day.",
            )
        return expected_data_points, coverage, quality_issues, "not_expected", "Non-trading day."

    if expected_series == 0:
        reason = (
            "Observed data; instrument is not in the current expected universe."
            if data_points
            else "Instrument is not in the current expected universe."
        )
        return expected_data_points, coverage, quality_issues, "not_expected", reason

    if expected_points_per_series == 0:
        return expected_data_points, coverage, quality_issues, "not_expected", "Collection is not due yet."

    if data_points == 0:
        return expected_data_points, 0.0, quality_issues, "missing", "No expected candles were found."

    if coverage is not None and coverage < 99:
        missing = max(expected_data_points - data_points, 0)
        return (
            expected_data_points,
            coverage,
            quality_issues,
            "attention",
            f"{missing} expected one-minute candle(s) are missing.",
        )

    return expected_data_points, coverage, quality_issues, "healthy", "Coverage and value checks passed."


class IndiaHubRepository:
    """Aggregate instrument-level and day-level India market checks."""

    def __init__(self, client: QueryClient) -> None:
        self.client = client

    @classmethod
    def from_environment(cls) -> IndiaHubRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def list_instruments(
        self,
        *,
        trading_date: date,
        instrument_id: UUID | None = None,
        scope: IndiaCollectionScope = "all",
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> IndiaHubInstrumentPage:
        checked_at = now or datetime.now(UTC)
        market_status, expected_points = _session_state(trading_date, checked_at)
        search_condition = ""
        instrument_condition = ""
        scope_condition = {
            "all": "",
            "collecting": "AND ifNull(expected.expected_series, 0) > 0",
            "historical": (
                "AND ifNull(expected.expected_series, 0) = 0 "
                "AND ifNull(history.data_points, 0) > 0"
            ),
            "not_configured": (
                "AND ifNull(expected.expected_series, 0) = 0 "
                "AND ifNull(history.data_points, 0) = 0"
            ),
        }[scope]
        parameters: dict[str, Any] = {
            "trading_date": trading_date,
            "limit": limit,
            "offset": offset,
        }
        if instrument_id:
            instrument_condition = "AND reference.instrument_id = {instrument_id:UUID}"
            parameters["instrument_id"] = instrument_id
        if search:
            search_condition = """
                AND (
                    positionCaseInsensitiveUTF8(reference.symbol, {search:String}) > 0
                    OR positionCaseInsensitiveUTF8(ifNull(reference.name, ''), {search:String}) > 0
                    OR positionCaseInsensitiveUTF8(ifNull(reference.instrument_key, ''), {search:String}) > 0
                )
            """
            parameters["search"] = search

        summary_result = self.client.query(
            """
            WITH reference AS (
                SELECT instrument_id
                FROM ref_instruments FINAL
                WHERE market_code = 'IND' AND status = 'active'
                GROUP BY instrument_id
            ), history AS (
                SELECT instrument_id, count() AS data_points,
                       max(ingested_at) AS last_ingested_at
                FROM market_candles_1min FINAL
                WHERE market_code = 'IND'
                GROUP BY instrument_id
            ), expected AS (
                SELECT instrument_id, count() AS expected_series
                FROM india_expected_series FINAL
                WHERE active
                GROUP BY instrument_id
            )
            SELECT count() AS reference_total,
                   countIf(ifNull(expected.expected_series, 0) > 0) AS collecting_total,
                   countIf(ifNull(expected.expected_series, 0) = 0
                           AND ifNull(history.data_points, 0) > 0) AS historical_total,
                   countIf(ifNull(expected.expected_series, 0) = 0
                           AND ifNull(history.data_points, 0) = 0) AS not_configured_total,
                   max(history.last_ingested_at) AS data_as_of
            FROM reference
            LEFT JOIN history USING (instrument_id)
            LEFT JOIN expected USING (instrument_id)
            SETTINGS join_use_nulls = 1
            """
        )
        summary_rows = _rows(summary_result)
        summary = summary_rows[0] if summary_rows else {}

        result = self.client.query(
            f"""
            WITH history AS (
                SELECT instrument_id,
                       argMax(symbol, bar_time) AS symbol,
                       count() AS data_points,
                       uniqExact(toDate(bar_time, 'Asia/Kolkata')) AS trading_days,
                       uniqExact(tuple(contract_id, source)) AS unique_series,
                       min(bar_time) AS first_bar_time,
                       max(bar_time) AS last_bar_time,
                       max(ingested_at) AS last_ingested_at,
                       countIf(toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}})
                           AS selected_data_points,
                       uniqExactIf(tuple(contract_id, source),
                           toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}})
                           AS selected_unique_series,
                       countIf(toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
                           AND (low > high OR open < low OR open > high
                                OR close < low OR close > high)) AS ohlc_violations,
                       countIf(toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
                           AND (isNull(open) OR isNull(high) OR isNull(low) OR isNull(close)))
                           AS null_ohlc_values,
                       countIf(toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
                           AND (toHour(toTimeZone(bar_time, 'Asia/Kolkata')) * 60
                                    + toMinute(toTimeZone(bar_time, 'Asia/Kolkata')) < 555
                                OR toHour(toTimeZone(bar_time, 'Asia/Kolkata')) * 60
                                    + toMinute(toTimeZone(bar_time, 'Asia/Kolkata')) >= 930))
                           AS outside_session
                FROM market_candles_1min FINAL
                WHERE market_code = 'IND'
                GROUP BY instrument_id
            ), reference AS (
                SELECT instrument_id,
                       instrument_key,
                       trading_symbol AS symbol,
                       name,
                       exchange_code,
                       segment,
                       instrument_type,
                       status,
                       source
                FROM ref_instruments FINAL
                WHERE market_code = 'IND' AND status = 'active'
            ), expected AS (
                SELECT instrument_id, count() AS expected_series
                FROM india_expected_series FINAL
                WHERE active
                GROUP BY instrument_id
            ), versions AS (
                SELECT instrument_id,
                       count() - uniqExact(tuple(contract_id, bar_time, source))
                           AS duplicate_versions
                FROM market_candles_1min
                WHERE market_code = 'IND'
                  AND toDate(bar_time, 'Asia/Kolkata') = {{trading_date:Date}}
                GROUP BY instrument_id
            )
            SELECT reference.instrument_id AS instrument_id, reference.symbol AS symbol,
                   reference.name AS name, reference.exchange_code AS exchange_code,
                   reference.segment AS segment, reference.instrument_type AS instrument_type,
                   reference.status AS status, reference.source AS source,
                   ifNull(history.data_points, 0) AS data_points,
                   ifNull(history.trading_days, 0) AS trading_days,
                   ifNull(history.unique_series, 0) AS unique_series,
                   history.first_bar_time AS first_bar_time,
                   history.last_bar_time AS last_bar_time,
                   history.last_ingested_at AS last_ingested_at,
                   ifNull(expected.expected_series, 0) AS expected_series,
                   ifNull(history.selected_data_points, 0) AS selected_data_points,
                   ifNull(history.selected_unique_series, 0) AS selected_unique_series,
                   ifNull(history.ohlc_violations, 0) AS ohlc_violations,
                   ifNull(history.null_ohlc_values, 0) AS null_ohlc_values,
                   ifNull(history.outside_session, 0) AS outside_session,
                   ifNull(versions.duplicate_versions, 0)
                       AS duplicate_versions,
                   count() OVER () AS total
            FROM reference
            LEFT JOIN history USING (instrument_id)
            LEFT JOIN expected USING (instrument_id)
            LEFT JOIN versions USING (instrument_id)
            WHERE 1 = 1 {scope_condition} {instrument_condition} {search_condition}
            ORDER BY reference.symbol, reference.instrument_id
            LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            SETTINGS join_use_nulls = 1
            """,
            parameters=parameters,
        )

        items: list[IndiaHubInstrument] = []
        rows = _rows(result)
        for row in rows:
            expected_series = int(row["expected_series"])
            data_points = int(row["data_points"])
            collection_status: IndiaCollectionStatus = (
                "collecting"
                if expected_series > 0
                else "historical"
                if data_points > 0
                else "not_configured"
            )
            expected_total, coverage, issues, check_status, check_reason = _quality_result(
                market_status=market_status,
                data_points=int(row["selected_data_points"]),
                expected_series=expected_series,
                expected_points_per_series=expected_points,
                ohlc_violations=int(row["ohlc_violations"]),
                null_ohlc_values=int(row["null_ohlc_values"]),
                outside_session=int(row["outside_session"]),
                duplicate_versions=int(row["duplicate_versions"]),
            )
            items.append(
                IndiaHubInstrument(
                    **{key: value for key, value in row.items() if key != "total"},
                    collection_status=collection_status,
                    expected_data_points=expected_total,
                    coverage_percent=coverage,
                    quality_issue_count=issues,
                    check_status=check_status,
                    check_reason=check_reason,
                )
            )

        return IndiaHubInstrumentPage(
            trading_date=trading_date,
            market_status=market_status,
            expected_points_per_series=expected_points,
            scope=scope,
            items=items,
            total=int(rows[0]["total"]) if rows else 0,
            reference_total=int(summary.get("reference_total", 0)),
            collecting_total=int(summary.get("collecting_total", 0)),
            historical_total=int(summary.get("historical_total", 0)),
            not_configured_total=int(summary.get("not_configured_total", 0)),
            limit=limit,
            offset=offset,
            data_as_of=summary.get("data_as_of"),
        )

    def get_instrument(
        self,
        instrument_id: UUID,
        *,
        trading_date: date,
        now: datetime | None = None,
    ) -> IndiaHubInstrument:
        """Return one instrument with stored-history and selected-session checks."""

        page = self.list_instruments(
            instrument_id=instrument_id,
            trading_date=trading_date,
            limit=1,
            now=now,
        )
        if not page.items:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instrument not found")
        return page.items[0]

    def list_instrument_days(
        self,
        instrument_id: UUID,
        *,
        date_from: date,
        date_to: date,
        now: datetime | None = None,
    ) -> IndiaHubInstrumentDays:
        parameters = {
            "instrument_id": instrument_id,
            "date_from": date_from,
            "date_to": date_to,
        }
        result = self.client.query(
            """
            WITH latest AS (
                SELECT toDate(bar_time, 'Asia/Kolkata') AS trading_date,
                       count() AS data_points,
                       uniqExact(tuple(contract_id, source)) AS unique_series,
                       min(bar_time) AS first_bar_time,
                       max(bar_time) AS last_bar_time,
                       max(ingested_at) AS last_ingested_at,
                       countIf(low > high OR open < low OR open > high
                               OR close < low OR close > high) AS ohlc_violations,
                       countIf(isNull(open) OR isNull(high) OR isNull(low) OR isNull(close))
                           AS null_ohlc_values,
                       countIf(toHour(toTimeZone(bar_time, 'Asia/Kolkata')) * 60
                                   + toMinute(toTimeZone(bar_time, 'Asia/Kolkata')) < 555
                               OR toHour(toTimeZone(bar_time, 'Asia/Kolkata')) * 60
                                   + toMinute(toTimeZone(bar_time, 'Asia/Kolkata')) >= 930)
                           AS outside_session
                FROM market_candles_1min FINAL
                WHERE market_code = 'IND'
                  AND instrument_id = {instrument_id:UUID}
                  AND toDate(bar_time, 'Asia/Kolkata') >= {date_from:Date}
                  AND toDate(bar_time, 'Asia/Kolkata') <= {date_to:Date}
                GROUP BY trading_date
            ), versions AS (
                SELECT toDate(bar_time, 'Asia/Kolkata') AS trading_date,
                       count() - uniqExact(tuple(contract_id, bar_time, source))
                           AS duplicate_versions
                FROM market_candles_1min
                WHERE market_code = 'IND'
                  AND instrument_id = {instrument_id:UUID}
                  AND toDate(bar_time, 'Asia/Kolkata') >= {date_from:Date}
                  AND toDate(bar_time, 'Asia/Kolkata') <= {date_to:Date}
                GROUP BY trading_date
            )
            SELECT latest.trading_date, latest.data_points, latest.unique_series,
                   latest.first_bar_time, latest.last_bar_time, latest.last_ingested_at,
                   latest.ohlc_violations, latest.null_ohlc_values,
                   latest.outside_session, ifNull(versions.duplicate_versions, 0)
                       AS duplicate_versions,
                   (SELECT count() FROM india_expected_series FINAL
                    WHERE instrument_id = {instrument_id:UUID} AND active) AS expected_series
            FROM latest
            LEFT JOIN versions USING (trading_date)
            ORDER BY trading_date DESC
            """,
            parameters=parameters,
        )
        observed = {row["trading_date"]: row for row in _rows(result)}
        calendar = xcals.get_calendar("XBOM")
        sessions = {
            stamp.date()
            for stamp in calendar.sessions_in_range(pd.Timestamp(date_from), pd.Timestamp(date_to))
        }
        all_dates = sorted(sessions | set(observed), reverse=True)
        checked_at = now or datetime.now(UTC)
        expected_series = int(next(iter(observed.values()))["expected_series"]) if observed else 0
        if not observed:
            expected_result = self.client.query(
                """
                SELECT count() AS expected_series FROM india_expected_series FINAL
                WHERE instrument_id = {instrument_id:UUID} AND active
                """,
                parameters={"instrument_id": instrument_id},
            )
            expected_rows = _rows(expected_result)
            expected_series = int(expected_rows[0]["expected_series"]) if expected_rows else 0

        items = []
        for trading_day in all_dates:
            row = observed.get(trading_day, {})
            market_status, expected_points = _session_state(trading_day, checked_at)
            data_points = int(row.get("data_points", 0))
            ohlc_violations = int(row.get("ohlc_violations", 0))
            null_ohlc_values = int(row.get("null_ohlc_values", 0))
            outside_session = int(row.get("outside_session", 0))
            duplicate_versions = int(row.get("duplicate_versions", 0))
            expected_total, coverage, issues, check_status, check_reason = _quality_result(
                market_status=market_status,
                data_points=data_points,
                expected_series=expected_series,
                expected_points_per_series=expected_points,
                ohlc_violations=ohlc_violations,
                null_ohlc_values=null_ohlc_values,
                outside_session=outside_session,
                duplicate_versions=duplicate_versions,
            )
            items.append(
                IndiaHubInstrumentDay(
                    trading_date=trading_day,
                    market_status=market_status,
                    data_points=data_points,
                    unique_series=int(row.get("unique_series", 0)),
                    expected_series=expected_series,
                    expected_data_points=expected_total,
                    coverage_percent=coverage,
                    first_bar_time=row.get("first_bar_time"),
                    last_bar_time=row.get("last_bar_time"),
                    last_ingested_at=row.get("last_ingested_at"),
                    ohlc_violations=ohlc_violations,
                    null_ohlc_values=null_ohlc_values,
                    outside_session=outside_session,
                    duplicate_versions=duplicate_versions,
                    quality_issue_count=issues,
                    check_status=check_status,
                    check_reason=check_reason,
                )
            )
        return IndiaHubInstrumentDays(
            instrument_id=instrument_id,
            date_from=date_from,
            date_to=date_to,
            items=items,
        )


def default_india_history_range(date_to: date) -> tuple[date, date]:
    return date_to - timedelta(days=35), date_to
