"""Operational read models and queries for alternative political data."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel

from factorlab.api.india_observability import IndiaIngestionRun, IndiaIngestionRunsPage
from factorlab.api.political import QueryClient
from factorlab.storage.clickhouse import ClickHouseStorage

_TRADES = """(
    SELECT political_trade_id, toString(political_trade_id) AS trade_key,
           country_code, chamber, filing_id, filing_url, filing_date,
           toYear(filing_date) AS filing_year, transaction_date, notification_date,
           bioguide_id, legislator_entity_id,
           legislator_name_raw AS legislator_name,
           ticker_raw AS ticker, listing_id, contract_id, asset_name_raw,
           transaction_type, amount_min, amount_max, source, ingested_at
    FROM alt.political_trades {final}
)"""
_FILINGS = "(SELECT * FROM alt.political_filings {final} WHERE chamber = 'house')"
_LEGISLATORS = """(
    SELECT t.bioguide_id, t.legislator_entity_id, e.legal_name AS official_full,
           t.chamber, t.state, t.district, t.party,
           t.latest_term_start AS term_start, t.term_end,
           t.in_office, t.source, t.ingested_at
    FROM (
        SELECT bioguide_id, argMax(legislator_entity_id, term_start) AS legislator_entity_id,
               argMax(chamber, term_start) AS chamber,
               argMax(state, term_start) AS state,
               argMax(district, term_start) AS district,
               argMax(party, term_start) AS party,
               max(term_start) AS latest_term_start,
               argMax(term_end, term_start) AS term_end,
               argMax(in_office, term_start) AS in_office,
               argMax(source, term_start) AS source,
               max(ingested_at) AS ingested_at
        FROM ref.legislator_terms FINAL GROUP BY bioguide_id
    ) AS t
    INNER JOIN ref.entities AS e FINAL ON e.entity_id = t.legislator_entity_id
)"""
_COMMITTEES = "(SELECT *, effective_to IS NULL AS is_current FROM alt.political_committees FINAL)"
_MEMBERSHIPS = "alt.political_committee_memberships"


class PoliticalDashboard(BaseModel):
    as_of_date: date
    current_legislators: int
    current_committees: int
    filings: int
    parsed_filings: int
    trades: int
    unique_legislators: int
    unique_tickers: int
    unparsed_filings: int
    legislator_match_rate: float
    ticker_resolution_rate: float
    late_disclosures: int
    anomaly_count: int
    latest_filing_date: date | None
    latest_transaction_date: date | None
    last_ingested_at: datetime | None
    collected_on_date: int


class PoliticalCoverageItem(BaseModel):
    chamber: str
    filings: int
    parsed_filings: int
    unparsed_filings: int
    filing_parse_rate: float
    trades: int
    unique_legislators: int
    matched_legislator_trades: int
    legislator_match_rate: float
    unique_tickers: int
    ticker_resolution_rate: float
    first_filing_date: date | None
    latest_filing_date: date | None
    last_ingested_at: datetime | None


class PoliticalCoveragePage(BaseModel):
    year: int | None
    items: list[PoliticalCoverageItem]


class PoliticalCollectionActivitySummary(BaseModel):
    ingestion_date: date
    filing_rows: int
    trade_rows: int
    unique_filings: int
    unique_trades: int
    first_ingested_at: datetime | None
    last_ingested_at: datetime | None


class PoliticalActivityBucket(BaseModel):
    bucket: str
    filing_rows: int
    trade_rows: int


class PoliticalCollectionActivity(BaseModel):
    summary: PoliticalCollectionActivitySummary
    by_hour: list[PoliticalActivityBucket]
    by_source: list[PoliticalActivityBucket]


class PoliticalFreshnessItem(BaseModel):
    dataset: str
    source: str
    rows: int
    last_ingested_at: datetime | None
    freshness_seconds: int | None
    status: str


class PoliticalFreshnessPage(BaseModel):
    checked_at: datetime
    stale_after_seconds: int
    items: list[PoliticalFreshnessItem]


class PoliticalAnomaly(BaseModel):
    anomaly_id: str
    anomaly_type: str
    severity: str
    record_type: str
    record_key: str
    legislator_name: str | None
    ticker: str | None
    event_date: date | None
    observed_value: str
    expected_value: str | None
    explanation: str


class PoliticalAnomaliesPage(BaseModel):
    items: list[PoliticalAnomaly]
    limit: int
    offset: int


class PoliticalMetricPoint(BaseModel):
    bucket: date
    value: float


class PoliticalMetricsSeries(BaseModel):
    metric: str
    date_basis: str
    group_by: str
    points: list[PoliticalMetricPoint]


class PoliticalLegislator(BaseModel):
    legislator_entity_id: UUID
    bioguide_id: str
    official_full: str
    chamber: str
    state: str
    district: int | None
    party: str
    term_start: date
    term_end: date
    in_office: bool
    source: str
    ingested_at: datetime


class PoliticalLegislatorsPage(BaseModel):
    items: list[PoliticalLegislator]
    limit: int
    offset: int


class PoliticalTicker(BaseModel):
    ticker: str
    trades: int
    filings: int
    legislators: int
    purchases: int
    sales: int
    amount_min_total: int
    amount_max_total: int
    first_transaction_date: date
    latest_transaction_date: date
    last_ingested_at: datetime


class PoliticalTickersPage(BaseModel):
    items: list[PoliticalTicker]
    limit: int
    offset: int


class PoliticalLegislatorSummary(BaseModel):
    legislator: PoliticalLegislator
    filings: int
    trades: int
    unique_tickers: int
    purchases: int
    sales: int
    amount_min_total: int
    amount_max_total: int
    first_transaction_date: date | None
    latest_transaction_date: date | None
    last_ingested_at: datetime | None


class PoliticalTickerSummary(BaseModel):
    ticker: PoliticalTicker
    top_legislators: list[dict[str, Any]]
    transaction_types: list[dict[str, Any]]


class PoliticalSourceStatus(BaseModel):
    source: str
    pipeline: str
    run_status: str
    last_run_started_at: datetime
    last_run_completed_at: datetime | None
    last_success_at: datetime | None
    last_ingested_at: datetime | None
    freshness_seconds: int | None
    status: str


class PoliticalSourceStatusPage(BaseModel):
    checked_at: datetime
    items: list[PoliticalSourceStatus]


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


class PoliticalObservabilityRepository:
    """Read political collection coverage, quality, and ingestion health."""

    def __init__(self, client: QueryClient) -> None:
        self.client = client

    def _query(self, sql: str, parameters: dict[str, Any] | None = None):
        for old, projection in (
            ("alt_political_house_filings", _FILINGS),
            ("alt_political_trades", _TRADES),
        ):
            sql = sql.replace(f"{old} AS filings FINAL", projection.format(final="FINAL") + " AS filings")
            sql = sql.replace(f"{old} FINAL", projection.format(final="FINAL"))
            sql = sql.replace(old, projection.format(final=""))
        sql = sql.replace("alt_political_legislators FINAL", _LEGISLATORS)
        sql = sql.replace("alt_political_committees FINAL", _COMMITTEES)
        sql = sql.replace("alt_political_committee_memberships FINAL", _MEMBERSHIPS + " FINAL")
        sql = sql.replace("FROM ingestion_runs FINAL", "FROM meta.ingestion_runs FINAL")
        return self.client.query(sql, parameters=parameters)

    @classmethod
    def from_environment(cls) -> PoliticalObservabilityRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def get_dashboard(self, *, as_of_date: date) -> PoliticalDashboard:
        result = self._query(
            """
            WITH
                (SELECT count() FROM alt_political_trades FINAL
                 WHERE transaction_date <= {as_of_date:Date}) AS trade_count,
                (SELECT countIf(bioguide_id IS NOT NULL) FROM alt_political_trades FINAL
                 WHERE transaction_date <= {as_of_date:Date}) AS matched_trades,
                (SELECT countIf(ticker IS NOT NULL AND ticker != '') FROM alt_political_trades FINAL
                 WHERE transaction_date <= {as_of_date:Date}) AS ticker_trades
            SELECT
                (SELECT count() FROM alt_political_legislators FINAL WHERE in_office)
                    AS current_legislators,
                (SELECT count() FROM alt_political_committees FINAL WHERE is_current)
                    AS current_committees,
                (SELECT count() FROM alt_political_house_filings FINAL
                 WHERE filing_date <= {as_of_date:Date}) AS filings,
                (SELECT uniqExact(filing_id) FROM alt_political_trades FINAL
                 WHERE filing_date <= {as_of_date:Date}) AS parsed_filings,
                trade_count AS trades,
                (SELECT uniqExact(bioguide_id) FROM alt_political_trades FINAL
                 WHERE bioguide_id IS NOT NULL AND transaction_date <= {as_of_date:Date})
                    AS unique_legislators,
                (SELECT uniqExact(ticker) FROM alt_political_trades FINAL
                 WHERE ticker IS NOT NULL AND ticker != ''
                   AND transaction_date <= {as_of_date:Date}) AS unique_tickers,
                (SELECT count() FROM alt_political_house_filings AS filings FINAL
                 LEFT JOIN (SELECT DISTINCT filing_id FROM alt_political_trades FINAL) AS parsed
                    USING (filing_id)
                 WHERE (parsed.filing_id IS NULL OR empty(parsed.filing_id))
                   AND filings.filing_date <= {as_of_date:Date})
                    AS unparsed_filings,
                if(trade_count = 0, 100, matched_trades * 100.0 / trade_count)
                    AS legislator_match_rate,
                if(trade_count = 0, 100, ticker_trades * 100.0 / trade_count)
                    AS ticker_resolution_rate,
                (SELECT countIf(dateDiff('day', transaction_date, filing_date) > 45)
                 FROM alt_political_trades FINAL
                 WHERE transaction_date <= {as_of_date:Date}) AS late_disclosures,
                (SELECT countIf(transaction_date > filing_date
                                OR (notification_date IS NOT NULL
                                    AND notification_date < transaction_date)
                                OR (amount_min IS NOT NULL AND amount_max IS NOT NULL
                                    AND amount_min > amount_max)
                                OR empty(trim(asset_name_raw)))
                 FROM alt_political_trades FINAL
                 WHERE transaction_date <= {as_of_date:Date}) AS invalid_trades,
                (SELECT maxOrNull(filing_date) FROM alt_political_house_filings FINAL
                 WHERE filing_date <= {as_of_date:Date}) AS latest_filing_date,
                (SELECT maxOrNull(transaction_date) FROM alt_political_trades FINAL
                 WHERE transaction_date <= {as_of_date:Date}) AS latest_transaction_date,
                greatest(
                    (SELECT maxOrNull(ingested_at) FROM alt_political_house_filings FINAL),
                    (SELECT maxOrNull(ingested_at) FROM alt_political_trades FINAL)
                ) AS last_ingested_at,
                (SELECT count() FROM alt_political_house_filings
                 WHERE toDate(ingested_at) = {as_of_date:Date})
                + (SELECT count() FROM alt_political_trades
                   WHERE toDate(ingested_at) = {as_of_date:Date}) AS collected_on_date
            """,
            parameters={"as_of_date": as_of_date},
        )
        row = _rows(result)[0]
        row["legislator_match_rate"] = round(float(row["legislator_match_rate"]), 2)
        row["ticker_resolution_rate"] = round(float(row["ticker_resolution_rate"]), 2)
        row["anomaly_count"] = (
            int(row.pop("invalid_trades"))
            + int(row["unparsed_filings"])
            + int(row["late_disclosures"])
        )
        return PoliticalDashboard(as_of_date=as_of_date, **row)

    def list_coverage(self, *, year: int | None = None) -> PoliticalCoveragePage:
        filing_filter = "" if year is None else "WHERE filing_year = {year:UInt16}"
        trade_filter = "" if year is None else "WHERE filing_year = {year:UInt16}"
        result = self._query(
            f"""
            WITH filings AS (
                SELECT 'house' AS chamber, count() AS filings,
                       min(filing_date) AS first_filing_date,
                       max(filing_date) AS latest_filing_date,
                       max(ingested_at) AS filing_ingested_at
                FROM alt_political_house_filings FINAL {filing_filter}
                GROUP BY chamber
            ), trades AS (
                SELECT chamber, uniqExact(filing_id) AS parsed_filings, count() AS trades,
                       uniqExactIf(bioguide_id, bioguide_id IS NOT NULL) AS unique_legislators,
                       countIf(bioguide_id IS NOT NULL) AS matched_legislator_trades,
                       uniqExactIf(ticker, ticker IS NOT NULL AND ticker != '') AS unique_tickers,
                       countIf(ticker IS NOT NULL AND ticker != '') AS ticker_trades,
                       max(ingested_at) AS trade_ingested_at
                FROM alt_political_trades FINAL {trade_filter}
                GROUP BY chamber
            )
            SELECT coalesce(nullIf(filings.chamber, ''), trades.chamber) AS chamber,
                   ifNull(filings.filings, 0) AS filings,
                   ifNull(trades.parsed_filings, 0) AS parsed_filings,
                   greatest(ifNull(filings.filings, 0) - ifNull(trades.parsed_filings, 0), 0)
                       AS unparsed_filings,
                   if(ifNull(filings.filings, 0) = 0, 100,
                      least(ifNull(trades.parsed_filings, 0) * 100.0
                            / ifNull(filings.filings, 0), 100))
                       AS filing_parse_rate,
                   ifNull(trades.trades, 0) AS trades,
                   ifNull(trades.unique_legislators, 0) AS unique_legislators,
                   ifNull(trades.matched_legislator_trades, 0) AS matched_legislator_trades,
                   if(ifNull(trades.trades, 0) = 0, 100,
                      ifNull(trades.matched_legislator_trades, 0) * 100.0
                      / ifNull(trades.trades, 0))
                       AS legislator_match_rate,
                   ifNull(trades.unique_tickers, 0) AS unique_tickers,
                   if(ifNull(trades.trades, 0) = 0, 100,
                      ifNull(trades.ticker_trades, 0) * 100.0 / ifNull(trades.trades, 0))
                       AS ticker_resolution_rate,
                   filings.first_filing_date, filings.latest_filing_date,
                   greatest(filings.filing_ingested_at, trades.trade_ingested_at)
                       AS last_ingested_at
            FROM filings FULL OUTER JOIN trades USING (chamber)
            ORDER BY chamber
            """,
            parameters={"year": year} if year is not None else {},
        )
        items = []
        for row in _rows(result):
            for field in ("filing_parse_rate", "legislator_match_rate", "ticker_resolution_rate"):
                row[field] = round(float(row[field]), 2)
            items.append(PoliticalCoverageItem.model_validate(row))
        return PoliticalCoveragePage(year=year, items=items)

    def get_collection_activity(self, *, ingestion_date: date) -> PoliticalCollectionActivity:
        parameters = {"ingestion_date": ingestion_date}
        summary = self._query(
            """
            SELECT
                (SELECT count() FROM alt_political_house_filings
                 WHERE toDate(ingested_at) = {ingestion_date:Date}) AS filing_rows,
                (SELECT count() FROM alt_political_trades
                 WHERE toDate(ingested_at) = {ingestion_date:Date}) AS trade_rows,
                (SELECT uniqExact(filing_id) FROM alt_political_house_filings
                 WHERE toDate(ingested_at) = {ingestion_date:Date}) AS unique_filings,
                (SELECT uniqExact(trade_key) FROM alt_political_trades
                 WHERE toDate(ingested_at) = {ingestion_date:Date}) AS unique_trades,
                least(
                    (SELECT minOrNull(ingested_at) FROM alt_political_house_filings
                     WHERE toDate(ingested_at) = {ingestion_date:Date}),
                    (SELECT minOrNull(ingested_at) FROM alt_political_trades
                     WHERE toDate(ingested_at) = {ingestion_date:Date})
                ) AS first_ingested_at,
                greatest(
                    (SELECT maxOrNull(ingested_at) FROM alt_political_house_filings
                     WHERE toDate(ingested_at) = {ingestion_date:Date}),
                    (SELECT maxOrNull(ingested_at) FROM alt_political_trades
                     WHERE toDate(ingested_at) = {ingestion_date:Date})
                ) AS last_ingested_at
            """,
            parameters=parameters,
        )
        hourly = self._query(
            """
            SELECT bucket, sum(filing_rows) AS filing_rows, sum(trade_rows) AS trade_rows
            FROM (
                SELECT formatDateTime(toStartOfHour(ingested_at), '%H:00') AS bucket,
                       count() AS filing_rows, 0 AS trade_rows
                FROM alt_political_house_filings
                WHERE toDate(ingested_at) = {ingestion_date:Date} GROUP BY bucket
                UNION ALL
                SELECT formatDateTime(toStartOfHour(ingested_at), '%H:00') AS bucket,
                       0 AS filing_rows, count() AS trade_rows
                FROM alt_political_trades
                WHERE toDate(ingested_at) = {ingestion_date:Date} GROUP BY bucket
            ) GROUP BY bucket ORDER BY bucket
            """,
            parameters=parameters,
        )
        sources = self._query(
            """
            SELECT bucket, sum(filing_rows) AS filing_rows, sum(trade_rows) AS trade_rows
            FROM (
                SELECT source AS bucket, count() AS filing_rows, 0 AS trade_rows
                FROM alt_political_house_filings
                WHERE toDate(ingested_at) = {ingestion_date:Date} GROUP BY source
                UNION ALL
                SELECT source AS bucket, 0 AS filing_rows, count() AS trade_rows
                FROM alt_political_trades
                WHERE toDate(ingested_at) = {ingestion_date:Date} GROUP BY source
            ) GROUP BY bucket ORDER BY bucket
            """,
            parameters=parameters,
        )
        return PoliticalCollectionActivity(
            summary=PoliticalCollectionActivitySummary(
                ingestion_date=ingestion_date, **_rows(summary)[0]
            ),
            by_hour=[PoliticalActivityBucket.model_validate(row) for row in _rows(hourly)],
            by_source=[PoliticalActivityBucket.model_validate(row) for row in _rows(sources)],
        )

    def list_freshness(self, *, stale_after_seconds: int) -> PoliticalFreshnessPage:
        checked_at = datetime.now(UTC)
        result = self._query(
            """
            SELECT dataset, source, rows, last_ingested_at FROM (
                SELECT 'legislators' AS dataset, 'congress_legislators' AS source,
                       count() AS rows,
                       maxOrNull(ingested_at) AS last_ingested_at
                FROM alt_political_legislators FINAL
                UNION ALL
                SELECT 'committees', 'congress_legislators', count(), maxOrNull(ingested_at)
                FROM alt_political_committees FINAL
                UNION ALL
                SELECT 'memberships', 'congress_legislators', count(), maxOrNull(ingested_at)
                FROM alt_political_committee_memberships FINAL
                UNION ALL
                SELECT 'filings', 'house_clerk_filing_index', count(), maxOrNull(ingested_at)
                FROM alt_political_house_filings FINAL
                UNION ALL
                SELECT 'trades', 'house_clerk_ptr', count(), maxOrNull(ingested_at)
                FROM alt_political_trades FINAL
            ) ORDER BY dataset, source
            """
        )
        items = []
        for row in _rows(result):
            age = _age_seconds(row["last_ingested_at"], checked_at)
            freshness_status = (
                "never_seen" if age is None else "stale" if age > stale_after_seconds else "fresh"
            )
            items.append(
                PoliticalFreshnessItem(
                    **row,
                    freshness_seconds=age,
                    status=freshness_status,
                )
            )
        return PoliticalFreshnessPage(
            checked_at=checked_at,
            stale_after_seconds=stale_after_seconds,
            items=items,
        )

    def list_anomalies(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        severity: str | None = None,
        anomaly_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> PoliticalAnomaliesPage:
        conditions = []
        parameters: dict[str, Any] = {}
        if date_from:
            conditions.append("event_date >= {date_from:Date}")
            parameters["date_from"] = date_from
        if date_to:
            conditions.append("event_date <= {date_to:Date}")
            parameters["date_to"] = date_to
        if severity:
            conditions.append("severity = {severity:String}")
            parameters["severity"] = severity
        if anomaly_type:
            conditions.append("anomaly_type = {anomaly_type:String}")
            parameters["anomaly_type"] = anomaly_type
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        result = self._query(
            f"""
            WITH parsed AS (SELECT DISTINCT filing_id FROM alt_political_trades FINAL)
            SELECT * FROM (
                SELECT 'unparsed_filing' AS anomaly_type, 'warning' AS severity,
                       'filing' AS record_type, filings.filing_id AS record_key,
                       filings.filer_name_raw AS legislator_name,
                       CAST(NULL, 'Nullable(String)') AS ticker,
                       filings.filing_date AS event_date, 'not parsed' AS observed_value,
                       'parsed' AS expected_value,
                       'A filing index row has no parsed trade records.' AS explanation
                FROM alt_political_house_filings AS filings FINAL
                LEFT JOIN parsed USING (filing_id)
                WHERE parsed.filing_id IS NULL OR empty(parsed.filing_id)
                UNION ALL
                SELECT 'late_disclosure', 'warning', 'trade', trade_key, legislator_name,
                       ticker, transaction_date,
                       toString(dateDiff('day', transaction_date, filing_date)), '<=45 days',
                       'The filing date is more than 45 days after the transaction date.'
                FROM alt_political_trades FINAL
                WHERE dateDiff('day', transaction_date, filing_date) > 45
                UNION ALL
                SELECT 'transaction_after_filing', 'critical', 'trade', trade_key,
                       legislator_name, ticker, transaction_date,
                       toString(transaction_date), toString(filing_date),
                       'The transaction date occurs after its filing date.'
                FROM alt_political_trades FINAL WHERE transaction_date > filing_date
                UNION ALL
                SELECT 'notification_before_transaction', 'critical', 'trade', trade_key,
                       legislator_name, ticker, transaction_date,
                       toString(notification_date), toString(transaction_date),
                       'The notification date occurs before the transaction date.'
                FROM alt_political_trades FINAL
                WHERE notification_date IS NOT NULL AND notification_date < transaction_date
                UNION ALL
                SELECT 'invalid_amount_range', 'critical', 'trade', trade_key,
                       legislator_name, ticker, transaction_date,
                       concat(toString(amount_min), '-', toString(amount_max)), 'min <= max',
                       'The disclosed minimum amount is greater than the maximum.'
                FROM alt_political_trades FINAL
                WHERE amount_min IS NOT NULL AND amount_max IS NOT NULL AND amount_min > amount_max
                UNION ALL
                SELECT 'unmatched_legislator', 'warning', 'trade', trade_key,
                       legislator_name, ticker, transaction_date, 'null', 'bioguide_id',
                       'The trade could not be resolved to a canonical legislator.'
                FROM alt_political_trades FINAL WHERE bioguide_id IS NULL
            ) {where}
            ORDER BY event_date DESC, record_key DESC
            LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            """,
            parameters={**parameters, "limit": limit, "offset": offset},
        )
        items = []
        for row in _rows(result):
            identity = f"{row['anomaly_type']}|{row['record_type']}|{row['record_key']}"
            row["anomaly_id"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
            items.append(PoliticalAnomaly.model_validate(row))
        return PoliticalAnomaliesPage(items=items, limit=limit, offset=offset)

    def get_metrics(
        self,
        *,
        metric: str,
        date_basis: str,
        group_by: str,
        date_from: date,
        date_to: date,
    ) -> PoliticalMetricsSeries:
        table = "alt_political_house_filings" if metric == "filings" else "alt_political_trades"
        date_column = {
            "filing": "filing_date",
            "transaction": "transaction_date",
            "ingestion": "toDate(ingested_at)",
        }[date_basis]
        if table == "alt_political_house_filings" and date_basis == "transaction":
            raise ValueError("filings cannot be grouped by transaction date")
        bucket = f"toStartOfMonth({date_column})" if group_by == "month" else date_column
        expression = {
            "filings": "count()",
            "trades": "count()",
            "unique_legislators": "uniqExactIf(bioguide_id, bioguide_id IS NOT NULL)",
            "unique_tickers": "uniqExactIf(ticker, ticker IS NOT NULL AND ticker != '')",
            "amount_min": "sum(ifNull(amount_min, 0))",
            "late_disclosures": "countIf(dateDiff('day', transaction_date, filing_date) > 45)",
            "unmatched_legislators": "countIf(bioguide_id IS NULL)",
        }[metric]
        result = self._query(
            f"""
            SELECT {bucket} AS bucket, toFloat64({expression}) AS value
            FROM {table} FINAL
            WHERE {date_column} >= {{date_from:Date}} AND {date_column} <= {{date_to:Date}}
            GROUP BY bucket ORDER BY bucket
            """,
            parameters={"date_from": date_from, "date_to": date_to},
        )
        return PoliticalMetricsSeries(
            metric=metric,
            date_basis=date_basis,
            group_by=group_by,
            points=[PoliticalMetricPoint.model_validate(row) for row in _rows(result)],
        )

    def list_legislators(
        self,
        *,
        search: str | None = None,
        chamber: str | None = None,
        state_code: str | None = None,
        party: str | None = None,
        in_office: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> PoliticalLegislatorsPage:
        conditions = []
        parameters: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            conditions.append("positionCaseInsensitiveUTF8(official_full, {search:String}) > 0")
            parameters["search"] = search
        for value, field in [(chamber, "chamber"), (state_code, "state"), (party, "party")]:
            if value:
                conditions.append(f"{field} = {{{field}:String}}")
                parameters[field] = value
        if in_office is not None:
            conditions.append("in_office = {in_office:Bool}")
            parameters["in_office"] = in_office
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        result = self._query(
            f"""
            SELECT legislator_entity_id, bioguide_id, official_full, chamber, state, district, party,
                   term_start, term_end, in_office, source, ingested_at
            FROM alt_political_legislators FINAL {where}
            ORDER BY official_full, bioguide_id
            LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            """,
            parameters=parameters,
        )
        return PoliticalLegislatorsPage(
            items=[PoliticalLegislator.model_validate(row) for row in _rows(result)],
            limit=limit,
            offset=offset,
        )

    def list_tickers(
        self,
        *,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> PoliticalTickersPage:
        conditions = ["ticker IS NOT NULL", "ticker != ''"]
        parameters: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            conditions.append("positionCaseInsensitiveUTF8(ticker, {search:String}) > 0")
            parameters["search"] = search
        result = self._query(
            f"""
            SELECT ticker, count() AS trades, uniqExact(filing_id) AS filings,
                   uniqExactIf(bioguide_id, bioguide_id IS NOT NULL) AS legislators,
                   countIf(transaction_type = 'purchase') AS purchases,
                   countIf(startsWith(transaction_type, 'sale')) AS sales,
                   sum(ifNull(amount_min, 0)) AS amount_min_total,
                   sum(ifNull(amount_max, 0)) AS amount_max_total,
                   min(transaction_date) AS first_transaction_date,
                   max(transaction_date) AS latest_transaction_date,
                   max(ingested_at) AS last_ingested_at
            FROM alt_political_trades FINAL WHERE {" AND ".join(conditions)}
            GROUP BY ticker ORDER BY trades DESC, ticker
            LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            """,
            parameters=parameters,
        )
        return PoliticalTickersPage(
            items=[PoliticalTicker.model_validate(row) for row in _rows(result)],
            limit=limit,
            offset=offset,
        )

    def get_legislator_summary(self, bioguide_id: str) -> PoliticalLegislatorSummary:
        legislator_result = self._query(
            """
            SELECT legislator_entity_id, bioguide_id, official_full, chamber, state, district, party,
                   term_start, term_end, in_office, source, ingested_at
            FROM alt_political_legislators FINAL
            WHERE bioguide_id = {bioguide_id:String} LIMIT 1
            """,
            parameters={"bioguide_id": bioguide_id},
        )
        legislators = _rows(legislator_result)
        if not legislators:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Legislator not found"
            )
        stats = self._query(
            """
            SELECT (SELECT count() FROM alt_political_house_filings FINAL
                    WHERE bioguide_id = {bioguide_id:Nullable(String)}) AS filings,
                   count() AS trades,
                   uniqExactIf(ticker, ticker IS NOT NULL AND ticker != '') AS unique_tickers,
                   countIf(transaction_type = 'purchase') AS purchases,
                   countIf(startsWith(transaction_type, 'sale')) AS sales,
                   sum(ifNull(amount_min, 0)) AS amount_min_total,
                   sum(ifNull(amount_max, 0)) AS amount_max_total,
                   minOrNull(transaction_date) AS first_transaction_date,
                   maxOrNull(transaction_date) AS latest_transaction_date,
                   maxOrNull(ingested_at) AS last_ingested_at
            FROM alt_political_trades FINAL WHERE bioguide_id = {bioguide_id:String}
            """,
            parameters={"bioguide_id": bioguide_id},
        )
        return PoliticalLegislatorSummary(
            legislator=PoliticalLegislator.model_validate(legislators[0]),
            **_rows(stats)[0],
        )

    def get_ticker_summary(self, ticker: str) -> PoliticalTickerSummary:
        ticker_page = self.list_tickers(search=ticker, limit=1000)
        ticker_item = next((item for item in ticker_page.items if item.ticker == ticker), None)
        if ticker_item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticker not found")
        parameters = {"ticker": ticker}
        legislators = self._query(
            """
            SELECT legislator_name, bioguide_id, count() AS trades,
                   sum(ifNull(amount_min, 0)) AS amount_min_total
            FROM alt_political_trades FINAL WHERE ticker = {ticker:Nullable(String)}
            GROUP BY legislator_name, bioguide_id ORDER BY trades DESC LIMIT 20
            """,
            parameters=parameters,
        )
        transaction_types = self._query(
            """
            SELECT transaction_type, count() AS trades
            FROM alt_political_trades FINAL WHERE ticker = {ticker:Nullable(String)}
            GROUP BY transaction_type ORDER BY trades DESC
            """,
            parameters=parameters,
        )
        return PoliticalTickerSummary(
            ticker=ticker_item,
            top_legislators=_rows(legislators),
            transaction_types=_rows(transaction_types),
        )

    def list_ingestion_runs(self, **kwargs: Any) -> IndiaIngestionRunsPage:
        conditions = ["country_code = 'US' AND pipeline LIKE 'political%'"]
        parameters: dict[str, Any] = {
            "limit": kwargs.get("limit", 100),
            "offset": kwargs.get("offset", 0),
        }
        for value, field in [
            (kwargs.get("pipeline"), "pipeline"),
            (kwargs.get("source"), "source"),
            (kwargs.get("status_filter"), "status"),
        ]:
            if value:
                conditions.append(f"{field} = {{{field}:String}}")
                parameters[field] = value
        result = self._query(
            f"""
            SELECT run_id, pipeline, source, universe_id AS universe,
                   status, started_at, completed_at,
                   requested_series, successful_series, failed_series, rows_written,
                   error, metadata_json
            FROM ingestion_runs FINAL WHERE {" AND ".join(conditions)}
            ORDER BY started_at DESC, run_id DESC
            LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}
            """,
            parameters=parameters,
        )
        return IndiaIngestionRunsPage(
            items=[IndiaIngestionRun.model_validate(row) for row in _rows(result)],
            limit=parameters["limit"],
            offset=parameters["offset"],
        )

    def get_ingestion_run(self, run_id: UUID) -> IndiaIngestionRun:
        result = self._query(
            """
            SELECT run_id, pipeline, source, universe_id AS universe,
                   status, started_at, completed_at,
                   requested_series, successful_series, failed_series, rows_written,
                   error, metadata_json
            FROM ingestion_runs FINAL
            WHERE country_code = 'US' AND pipeline LIKE 'political%' AND run_id = {run_id:UUID} LIMIT 1
            """,
            parameters={"run_id": run_id},
        )
        rows = _rows(result)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion run not found"
            )
        return IndiaIngestionRun.model_validate(rows[0])

    def list_source_status(self, *, stale_after_seconds: int) -> PoliticalSourceStatusPage:
        checked_at = datetime.now(UTC)
        result = self._query(
            """
            WITH data AS (
                SELECT 'political' AS source,
                       greatest(
                           (SELECT maxOrNull(ingested_at) FROM alt_political_house_filings FINAL),
                           (SELECT maxOrNull(ingested_at) FROM alt_political_trades FINAL)
                       ) AS last_ingested_at
            ), runs AS (
                SELECT source, pipeline, argMax(status, started_at) AS run_status,
                       max(started_at) AS last_run_started_at,
                       argMax(completed_at, started_at) AS last_run_completed_at,
                       maxIf(completed_at, status = 'success') AS last_success_at
                FROM ingestion_runs FINAL WHERE country_code = 'US' AND pipeline LIKE 'political%'
                GROUP BY source, pipeline
            )
            SELECT runs.source, runs.pipeline, runs.run_status, runs.last_run_started_at,
                   runs.last_run_completed_at, runs.last_success_at, data.last_ingested_at
            FROM runs CROSS JOIN data
            ORDER BY runs.source, runs.pipeline
            """
        )
        items = []
        for row in _rows(result):
            age = _age_seconds(row["last_ingested_at"], checked_at)
            run_status = row["run_status"]
            health = (
                "running"
                if run_status == "running"
                else "failed"
                if run_status in {"failed", "partial"}
                else "stale"
                if age is None or age > stale_after_seconds
                else "healthy"
            )
            items.append(
                PoliticalSourceStatus(
                    **row,
                    freshness_seconds=age,
                    status=health,
                )
            )
        return PoliticalSourceStatusPage(checked_at=checked_at, items=items)


def _age_seconds(timestamp: datetime | None, now: datetime) -> int | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max(int((now - timestamp.astimezone(UTC)).total_seconds()), 0)
