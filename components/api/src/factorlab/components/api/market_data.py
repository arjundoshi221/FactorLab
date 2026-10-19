"""Configured market universes and per-listing stored coverage for the private hub."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import exchange_calendars as xcals
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from factorlab.clickhouse import ClickHouse
from factorlab.components.api.india import INDIA_BARS_SQL
from factorlab.components.api.india_hub import _rows

Market = Literal["US", "IN"]
Stream = Literal["daily", "historical"]
DataFilter = Literal["all", "has_data", "no_data"]


class UniverseTicker(BaseModel):
    listing_id: UUID
    contract_id: UUID | None = None
    symbol: str
    name: str | None
    source: str
    universe: str
    resolution: str
    series_count: int
    rows: int
    trading_days: int
    first_date: date | None
    latest_date: date | None
    last_ingested_at: datetime | None
    history_complete: bool | None = None
    recovery_error: str | None = None


class UniversePage(BaseModel):
    market: Market
    stream: Stream
    configured_total: int
    total: int
    limit: int
    offset: int
    configuration_as_of: datetime | None
    items: list[UniverseTicker]


class TickerCoverage(UniverseTicker):
    gap_window_from: date | None
    gap_window_to: date | None
    missing_trading_days: int | None


def _bars(market: Market) -> str:
    if market == "IN":
        return f"(SELECT listing_id, contract_id, country_code, '1min' AS resolution, trade_date, ingested_at FROM ({INDIA_BARS_SQL}))"
    return "(SELECT listing_id, CAST(NULL, 'Nullable(UUID)') AS contract_id, country_code, resolution, trade_date, ingested_at FROM market.bars FINAL)"


def _resolution(market: Market) -> str:
    return "1min" if market == "IN" else "daily"


class MarketDataRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_environment(cls) -> MarketDataRepository:
        return cls(ClickHouse.from_environment().client)

    def query(self, sql: str, **parameters: Any) -> list[dict[str, Any]]:
        return _rows(self.client.query(sql, parameters=parameters))

    def list_universe(
        self,
        *,
        market: Market,
        stream: Stream,
        search: str,
        data_filter: DataFilter,
        limit: int,
        offset: int,
    ) -> UniversePage:
        resolution = _resolution(market)
        parameters = {
            "market": market,
            "resolution": resolution,
            "search": search,
            "limit": limit,
            "offset": offset,
            "cutoff": datetime.now(UTC).date() - timedelta(days=29),
        }
        bars = _bars(market)
        date_sql = "AND b.trade_date >= {cutoff:Date}" if stream == "daily" else ""
        configured = """SELECT listing_id, contract_id, any(symbol) AS symbol,
                   arrayStringConcat(arraySort(groupUniqArray(source)), ', ') AS source,
                   arrayStringConcat(arraySort(groupUniqArray(universe)), ', ') AS universe,
                   count() AS series_count,
                   max(ingested_at) AS configuration_as_of
                   FROM meta.expected_series FINAL
                   WHERE country_code = {market:String} AND resolution = {resolution:String} AND active
                   GROUP BY listing_id, contract_id"""
        search_sql = """AND (positionCaseInsensitiveUTF8(c.symbol, {search:String}) > 0
                         OR positionCaseInsensitiveUTF8(ifNull(e.legal_name, ''), {search:String}) > 0)"""
        data_sql = ""
        if data_filter != "all":
            operator = "IN" if data_filter == "has_data" else "NOT IN"
            data_sql = f"""AND tuple(c.listing_id, ifNull(c.contract_id, toUUID('00000000-0000-0000-0000-000000000000'))) {operator} (
                SELECT DISTINCT tuple(b.listing_id, ifNull(b.contract_id, toUUID('00000000-0000-0000-0000-000000000000'))) FROM {bars} AS b
                WHERE b.country_code = {{market:String}} AND b.resolution = {{resolution:String}}
                {date_sql})"""
        scope = f"""FROM ({configured}) AS c
                    LEFT JOIN ref.listings AS l FINAL ON l.listing_id = c.listing_id
                    LEFT JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
                    LEFT JOIN ref.entities AS e FINAL ON e.entity_id = s.entity_id
                    WHERE 1 = 1 {search_sql} {data_sql}"""
        summary = self.query(
            f"""SELECT count() AS configured_total, maxOrNull(configuration_as_of) AS configuration_as_of
                FROM ({configured})""",
            **parameters,
        )[0]
        total = self.query(f"SELECT count() AS total {scope}", **parameters)[0]["total"]
        entries = self.query(
            f"""SELECT c.listing_id AS listing_id, c.contract_id AS contract_id, c.symbol AS symbol,
                       e.legal_name AS name, c.source AS source, c.universe AS universe,
                       c.series_count AS series_count
                {scope} ORDER BY c.symbol, c.listing_id, c.contract_id
                LIMIT {{limit:UInt16}} OFFSET {{offset:UInt32}}""",
            **parameters,
        )
        items = self._coverage(market, stream, resolution, entries)
        return UniversePage(
            market=market,
            stream=stream,
            configured_total=int(summary["configured_total"]),
            total=int(total),
            limit=limit,
            offset=offset,
            configuration_as_of=summary["configuration_as_of"],
            items=items,
        )

    def _coverage(
        self,
        market: Market,
        stream: Stream,
        resolution: str,
        entries: list[dict[str, Any]],
    ) -> list[UniverseTicker]:
        if not entries:
            return []
        ids = [row["listing_id"] for row in entries]
        bars = _bars(market)
        cutoff = datetime.now(UTC).date() - timedelta(days=29)
        date_sql = "AND b.trade_date >= {cutoff:Date}" if stream == "daily" else ""
        stats = self.query(
            f"""SELECT listing_id, contract_id, count() AS rows, uniqExact(trade_date) AS trading_days,
                       min(trade_date) AS first_date, max(trade_date) AS latest_date,
                       max(ingested_at) AS last_ingested_at
                FROM {bars} AS b WHERE b.country_code = {{market:String}}
                  AND b.resolution = {{resolution:String}} AND b.listing_id IN {{ids:Array(UUID)}}
                  {date_sql}
                GROUP BY listing_id, contract_id""",
            market=market,
            resolution=resolution,
            ids=ids,
            cutoff=cutoff,
        )
        recovery = self.query(
            """SELECT listing_id, argMax(history_complete, ingested_at) AS history_complete,
                      argMax(error, ingested_at) AS recovery_error
               FROM meta.recovery_state FINAL
               WHERE country_code = {market:String} AND resolution = {resolution:String}
                 AND listing_id IN {ids:Array(UUID)} GROUP BY listing_id""",
            market=market,
            resolution=resolution,
            ids=ids,
        )
        by_id = {(row["listing_id"], row.get("contract_id")): row for row in stats}
        recovery_by_id = {row["listing_id"]: row for row in recovery}
        items = []
        for entry in entries:
            contract_id = entry.get("contract_id")
            stored = by_id.get((entry["listing_id"], contract_id), {})
            # Recovery state is listing-scoped; it cannot describe an individual futures contract.
            state = recovery_by_id.get(entry["listing_id"], {}) if contract_id is None else {}
            items.append(
                UniverseTicker(
                    **entry,
                    resolution=resolution,
                    rows=int(stored.get("rows", 0)),
                    trading_days=int(stored.get("trading_days", 0)),
                    first_date=stored.get("first_date"),
                    latest_date=stored.get("latest_date"),
                    last_ingested_at=stored.get("last_ingested_at"),
                    history_complete=state.get("history_complete"),
                    recovery_error=state.get("recovery_error"),
                )
            )
        return items

    def ticker(
        self, market: Market, stream: Stream, listing_id: UUID, contract_id: UUID | None = None
    ) -> TickerCoverage:
        resolution = _resolution(market)
        contract_sql = (
            "AND contract_id = {contract_id:UUID}" if contract_id else "AND contract_id IS NULL"
        )
        configured = self.query(
            f"""SELECT listing_id, contract_id, any(symbol) AS symbol,
                      arrayStringConcat(arraySort(groupUniqArray(source)), ', ') AS source,
                      arrayStringConcat(arraySort(groupUniqArray(universe)), ', ') AS universe,
                      count() AS series_count
               FROM meta.expected_series FINAL WHERE country_code = {{market:String}}
                 AND resolution = {{resolution:String}} AND active AND listing_id = {{id:UUID}}
                 {contract_sql}
               GROUP BY listing_id, contract_id""",
            market=market,
            resolution=resolution,
            id=listing_id,
            **({"contract_id": contract_id} if contract_id else {}),
        )
        if not configured:
            raise HTTPException(404, "Ticker is not in the configured universe")
        name = self.query(
            """SELECT e.legal_name AS name FROM ref.listings AS l FINAL
               LEFT JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
               LEFT JOIN ref.entities AS e FINAL ON e.entity_id = s.entity_id
               WHERE l.listing_id = {id:UUID} LIMIT 1""",
            id=listing_id,
        )
        entry = {**configured[0], "name": name[0]["name"] if name else None}
        item = self._coverage(market, stream, resolution, [entry])[0]
        if not item.latest_date:
            return TickerCoverage(
                **item.model_dump(),
                gap_window_from=None,
                gap_window_to=None,
                missing_trading_days=None,
            )
        end = item.latest_date
        start = max(item.first_date or end, end - timedelta(days=365))
        calendar = xcals.get_calendar("XBOM" if market == "IN" else "XNYS")
        start = max(start, calendar.first_session.date())
        end = min(end, calendar.last_session.date())
        if start > end:
            return TickerCoverage(
                **item.model_dump(),
                gap_window_from=None,
                gap_window_to=None,
                missing_trading_days=None,
            )
        observed_contract_sql = (
            "AND b.contract_id = {contract_id:UUID}" if contract_id else "AND b.contract_id IS NULL"
        )
        observed = self.query(
            f"""SELECT DISTINCT trade_date FROM {_bars(market)} AS b
                WHERE b.country_code = {{market:String}} AND b.resolution = {{resolution:String}}
                  AND b.listing_id = {{id:UUID}} {observed_contract_sql}
                  AND b.trade_date BETWEEN {{start:Date}} AND {{end:Date}}""",
            market=market,
            resolution=resolution,
            id=listing_id,
            **({"contract_id": contract_id} if contract_id else {}),
            start=start,
            end=end,
        )
        expected = {
            day.date() for day in calendar.sessions_in_range(start.isoformat(), end.isoformat())
        }
        actual = {row["trade_date"] for row in observed}
        return TickerCoverage(
            **item.model_dump(),
            gap_window_from=start,
            gap_window_to=end,
            missing_trading_days=len(expected - actual),
        )


def repository() -> MarketDataRepository:
    return MarketDataRepository.from_environment()


Repo = Annotated[MarketDataRepository, Depends(repository)]
router = APIRouter()


@router.get("/universe", response_model=UniversePage)
def universe(
    repo: Repo,
    market: Market = "US",
    stream: Stream = "daily",
    search: Annotated[str, Query(max_length=100)] = "",
    data_filter: DataFilter = "all",
    limit: Annotated[int, Query(ge=1, le=250)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> UniversePage:
    return repo.list_universe(
        market=market,
        stream=stream,
        search=search.strip(),
        data_filter=data_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/universe/{market}/{stream}/{listing_id}", response_model=TickerCoverage)
def ticker(
    repo: Repo, market: Market, stream: Stream, listing_id: UUID, contract_id: UUID | None = None
) -> TickerCoverage:
    return repo.ticker(market, stream, listing_id, contract_id)
