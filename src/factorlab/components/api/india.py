"""Read models and ClickHouse queries for Indian market candles."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel

from factorlab.storage.clickhouse import ClickHouseStorage


class IndiaCandle1Min(BaseModel):
    """One latest-version Indian one-minute market candle."""

    listing_id: UUID
    contract_id: UUID | None
    symbol: str
    country_code: str
    bar_time: datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None
    oi: int | None
    source: str
    as_of_time: datetime
    ingested_at: datetime


class IndiaCandlesPage(BaseModel):
    """A cursor-paginated one-minute candle response."""

    items: list[IndiaCandle1Min]
    next_cursor: str | None
    limit: int
    data_as_of: datetime | None


class IndiaInstrument(BaseModel):
    """One latest-version Indian reference instrument."""

    listing_id: UUID
    security_id: UUID
    trading_symbol: str
    name: str
    isin: str | None
    exchange_code: str
    security_type: str
    currency_code: str
    lot_size: int
    tick_size: Decimal | None
    status: str
    source: str
    first_seen: date
    last_seen: date
    ingested_at: datetime


class IndiaInstrumentsPage(BaseModel):
    """A cursor-paginated Indian instrument response."""

    items: list[IndiaInstrument]
    next_cursor: str | None
    limit: int
    data_as_of: datetime | None


class IndiaMarketStats(BaseModel):
    """Overall coverage metrics for Indian one-minute candles."""

    reference_instruments: int
    instruments_with_data: int
    unique_series: int
    data_points: int
    trading_days: int
    first_bar_time: datetime | None
    last_bar_time: datetime | None
    data_as_of: datetime | None


class IndiaDailyStats(BaseModel):
    """Coverage metrics for one Indian trading date."""

    trading_date: date
    data_points: int
    unique_instruments: int
    unique_series: int
    first_bar_time: datetime
    last_bar_time: datetime
    data_as_of: datetime


class IndiaDailyStatsPage(BaseModel):
    """Daily Indian candle coverage, newest trading date first."""

    items: list[IndiaDailyStats]
    limit: int


class QueryResult(Protocol):
    column_names: tuple[str, ...] | list[str]
    result_rows: list[tuple[Any, ...]]


class QueryClient(Protocol):
    def query(self, query: str, parameters: dict[str, Any] | None = None) -> QueryResult: ...


INDIA_BARS_SQL = """
    SELECT b.listing_id, CAST(NULL, 'Nullable(UUID)') AS contract_id,
           l.trading_symbol AS symbol, b.country_code, b.trade_date,
           b.bar_time, b.open, b.high, b.low, b.close, b.volume, b.oi,
           b.source, b.as_of_time, b.ingested_at
    FROM market.bars AS b FINAL
    INNER JOIN ref.listings AS l FINAL ON l.listing_id = b.listing_id
    WHERE b.country_code = 'IN' AND b.resolution = '1min'
    UNION ALL
    SELECT f.underlying_listing_id AS listing_id, toNullable(f.contract_id) AS contract_id,
           f.source_symbol AS symbol, f.country_code, f.trade_date,
           f.bar_time, f.open, f.high, f.low, f.close, f.volume, f.oi,
           f.source, f.as_of_time, f.ingested_at
    FROM market.futures_contract_bars AS f FINAL
    WHERE f.country_code = 'IN' AND f.resolution = '1min'
"""

INDIA_BAR_VERSIONS_SQL = (
    INDIA_BARS_SQL.replace("market.bars AS b FINAL", "market.bars AS b")
    .replace("market.futures_contract_bars AS f FINAL", "market.futures_contract_bars AS f")
)


class IndiaCandlesRepository:
    """Execute parameterized reads against canonical Indian market tables."""

    def __init__(self, client: QueryClient) -> None:
        self.client = client

    @classmethod
    def from_environment(cls) -> IndiaCandlesRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def list_candles(
        self,
        *,
        listing_id: UUID | None = None,
        symbol: str | None = None,
        trading_date: date | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        source: str | None = None,
        cursor: str | None = None,
        limit: int = 500,
    ) -> IndiaCandlesPage:
        conditions = ["country_code = 'IN'"]
        parameters: dict[str, Any] = {"fetch_limit": limit + 1}

        if listing_id:
            conditions.append("listing_id = {listing_id:UUID}")
            parameters["listing_id"] = listing_id
        if symbol:
            conditions.append("symbol = {symbol:String}")
            parameters["symbol"] = symbol.upper()
        if trading_date:
            conditions.append("trade_date = {trading_date:Date}")
            parameters["trading_date"] = trading_date
        if time_from:
            conditions.append("bar_time >= {time_from:DateTime64(3, 'UTC')}")
            parameters["time_from"] = time_from
        if time_to:
            conditions.append("bar_time <= {time_to:DateTime64(3, 'UTC')}")
            parameters["time_to"] = time_to
        if source:
            conditions.append("source = {source:String}")
            parameters["source"] = source
        if cursor:
            cursor_values = decode_cursor(cursor)
            conditions.append(
                "(bar_time, symbol, listing_id, ifNull(contract_id, toUUID('00000000-0000-0000-0000-000000000000')), source) < "
                "({cursor_time:DateTime64(3, 'UTC')}, {cursor_symbol:String}, "
                "{cursor_listing:UUID}, {cursor_contract:UUID}, {cursor_source:String})"
            )
            parameters.update(cursor_values)

        result = self.client.query(
            f"""
            SELECT
                listing_id, contract_id, symbol, country_code, bar_time,
                open, high, low, close, volume, oi, source, as_of_time, ingested_at
            FROM ({INDIA_BARS_SQL}) AS bars
            WHERE {' AND '.join(conditions)}
            ORDER BY bar_time DESC, symbol DESC, listing_id DESC,
                     ifNull(contract_id, toUUID('00000000-0000-0000-0000-000000000000')) DESC,
                     source DESC
            LIMIT {{fetch_limit:UInt16}}
            """,
            parameters=parameters,
        )
        rows = [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
        has_more = len(rows) > limit
        items = [IndiaCandle1Min.model_validate(row) for row in rows[:limit]]
        next_cursor = encode_cursor(items[-1]) if has_more and items else None
        return IndiaCandlesPage(
            items=items,
            next_cursor=next_cursor,
            limit=limit,
            data_as_of=max((item.as_of_time for item in items), default=None),
        )

    def list_instruments(
        self,
        *,
        search: str | None = None,
        status: str | None = None,
        source: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> IndiaInstrumentsPage:
        conditions = ["l.country_code = 'IN'"]
        parameters: dict[str, Any] = {"fetch_limit": limit + 1}

        if search:
            conditions.append(
                "(positionCaseInsensitiveUTF8(trading_symbol, {search:String}) > 0 OR "
                "positionCaseInsensitiveUTF8(e.legal_name, {search:String}) > 0 OR "
                "positionCaseInsensitiveUTF8(ifNull(s.isin, ''), {search:String}) > 0)"
            )
            parameters["search"] = search
        if status:
            conditions.append("if(l.active, 'active', 'inactive') = {status:String}")
            parameters["status"] = status
        if source:
            conditions.append("a.source = {source:String}")
            parameters["source"] = source
        if cursor:
            cursor_symbol, cursor_listing = decode_instrument_cursor(cursor)
            conditions.append(
                "(l.trading_symbol, l.listing_id) > "
                "({cursor_symbol:String}, {cursor_listing:UUID})"
            )
            parameters.update(
                cursor_symbol=cursor_symbol,
                cursor_listing=cursor_listing,
            )

        result = self.client.query(
            f"""
            SELECT
                l.listing_id, s.security_id AS security_id, l.trading_symbol,
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
            WHERE {' AND '.join(conditions)}
            ORDER BY l.trading_symbol ASC, l.listing_id ASC
            LIMIT {{fetch_limit:UInt16}}
            """,
            parameters=parameters,
        )
        rows = [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
        has_more = len(rows) > limit
        items = [IndiaInstrument.model_validate(row) for row in rows[:limit]]
        next_cursor = encode_instrument_cursor(items[-1]) if has_more and items else None
        return IndiaInstrumentsPage(
            items=items,
            next_cursor=next_cursor,
            limit=limit,
            data_as_of=max((item.ingested_at for item in items), default=None),
        )

    def get_stats(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        source: str | None = None,
    ) -> IndiaMarketStats:
        conditions, parameters = _candle_stats_filters(
            date_from=date_from,
            date_to=date_to,
            source=source,
        )
        result = self.client.query(
            f"""
            SELECT
                (SELECT uniqExact(listing_id)
                 FROM ref.listings FINAL
                 WHERE country_code = 'IN') AS reference_instruments,
                uniqExact(listing_id) AS instruments_with_data,
                uniqExact(tuple(listing_id, contract_id)) AS unique_series,
                count() AS data_points,
                uniqExact(trade_date) AS trading_days,
                minOrNull(bar_time) AS first_bar_time,
                maxOrNull(bar_time) AS last_bar_time,
                maxOrNull(as_of_time) AS data_as_of
            FROM ({INDIA_BARS_SQL}) AS bars
            WHERE {' AND '.join(conditions)}
            """,
            parameters=parameters,
        )
        row = dict(zip(result.column_names, result.result_rows[0], strict=True))
        return IndiaMarketStats.model_validate(row)

    def list_daily_stats(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        source: str | None = None,
        limit: int = 90,
    ) -> IndiaDailyStatsPage:
        conditions, parameters = _candle_stats_filters(
            date_from=date_from,
            date_to=date_to,
            source=source,
        )
        parameters["limit"] = limit
        result = self.client.query(
            f"""
            SELECT
                trade_date AS trading_date,
                count() AS data_points,
                uniqExact(listing_id) AS unique_instruments,
                uniqExact(tuple(listing_id, contract_id)) AS unique_series,
                min(bar_time) AS first_bar_time,
                max(bar_time) AS last_bar_time,
                max(as_of_time) AS data_as_of
            FROM ({INDIA_BARS_SQL}) AS bars
            WHERE {' AND '.join(conditions)}
            GROUP BY trading_date
            ORDER BY trading_date DESC
            LIMIT {{limit:UInt16}}
            """,
            parameters=parameters,
        )
        rows = [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
        return IndiaDailyStatsPage(
            items=[IndiaDailyStats.model_validate(row) for row in rows],
            limit=limit,
        )


def _candle_stats_filters(
    *,
    date_from: date | None,
    date_to: date | None,
    source: str | None,
) -> tuple[list[str], dict[str, Any]]:
    conditions = ["country_code = 'IN'"]
    parameters: dict[str, Any] = {}
    if date_from:
        conditions.append("trade_date >= {date_from:Date}")
        parameters["date_from"] = date_from
    if date_to:
        conditions.append("trade_date <= {date_to:Date}")
        parameters["date_to"] = date_to
    if source:
        conditions.append("source = {source:String}")
        parameters["source"] = source
    return conditions, parameters


def encode_cursor(candle: IndiaCandle1Min) -> str:
    payload = json.dumps(
        {
            "time": candle.bar_time.isoformat(),
            "symbol": candle.symbol,
            "v": 2,
            "listing_id": str(candle.listing_id),
            "contract_id": str(candle.contract_id) if candle.contract_id else None,
            "source": candle.source,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(payload, dict) or payload.get("v") != 2:
            raise HTTPException(400, "Legacy cursor; restart pagination with canonical listing IDs")
        cursor_time = datetime.fromisoformat(payload["time"])
        cursor_time = (
            cursor_time.replace(tzinfo=UTC)
            if cursor_time.tzinfo is None
            else cursor_time.astimezone(UTC)
        )
        return {
            "cursor_time": cursor_time,
            "cursor_symbol": str(payload["symbol"]),
            "cursor_listing": UUID(payload["listing_id"]),
            "cursor_contract": UUID(payload["contract_id"]) if payload["contract_id"] else UUID(int=0),
            "cursor_source": str(payload["source"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor",
        ) from exc


def encode_instrument_cursor(instrument: IndiaInstrument) -> str:
    payload = json.dumps(
        {
            "symbol": instrument.trading_symbol,
            "v": 2,
            "listing_id": str(instrument.listing_id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_instrument_cursor(cursor: str) -> tuple[str, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(payload, dict) or payload.get("v") != 2:
            raise HTTPException(400, "Legacy cursor; restart pagination with canonical listing IDs")
        symbol = payload["symbol"]
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("invalid instrument symbol")
        return symbol, UUID(payload["listing_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid instrument pagination cursor",
        ) from exc
