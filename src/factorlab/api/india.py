"""Read models and ClickHouse queries for Indian market candles."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel

from factorlab.storage.clickhouse import ClickHouseStorage


class IndiaCandle1Min(BaseModel):
    """One latest-version Indian one-minute market candle."""

    instrument_id: UUID
    contract_id: UUID
    symbol: str
    market_code: str
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

    instrument_id: UUID
    instrument_key: str
    trading_symbol: str
    name: str
    isin: str | None
    exchange_code: str
    segment: str
    instrument_type: str
    asset_class: str
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


class IndiaCandlesRepository:
    """Execute parameterized reads against ``market_candles_1min``."""

    def __init__(self, client: QueryClient) -> None:
        self.client = client

    @classmethod
    def from_environment(cls) -> IndiaCandlesRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def list_candles(
        self,
        *,
        instrument_id: UUID | None = None,
        symbol: str | None = None,
        trading_date: date | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        source: str | None = None,
        cursor: str | None = None,
        limit: int = 500,
    ) -> IndiaCandlesPage:
        conditions = ["market_code = 'IND'"]
        parameters: dict[str, Any] = {"fetch_limit": limit + 1}

        if instrument_id:
            conditions.append("instrument_id = {instrument_id:UUID}")
            parameters["instrument_id"] = instrument_id
        if symbol:
            conditions.append("symbol = {symbol:String}")
            parameters["symbol"] = symbol.upper()
        if trading_date:
            conditions.append("toDate(bar_time, 'Asia/Kolkata') = {trading_date:Date}")
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
                "(bar_time, symbol, instrument_id, contract_id, source) < "
                "({cursor_time:DateTime64(3, 'UTC')}, {cursor_symbol:String}, "
                "{cursor_instrument:UUID}, {cursor_contract:UUID}, {cursor_source:String})"
            )
            parameters.update(cursor_values)

        result = self.client.query(
            f"""
            SELECT
                instrument_id, contract_id, symbol, market_code, bar_time,
                open, high, low, close, volume, oi, source, as_of_time, ingested_at
            FROM market_candles_1min FINAL
            WHERE {' AND '.join(conditions)}
            ORDER BY bar_time DESC, symbol DESC, instrument_id DESC, contract_id DESC, source DESC
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
        conditions = ["market_code = 'IND'"]
        parameters: dict[str, Any] = {"fetch_limit": limit + 1}

        if search:
            conditions.append(
                "(positionCaseInsensitiveUTF8(trading_symbol, {search:String}) > 0 OR "
                "positionCaseInsensitiveUTF8(name, {search:String}) > 0 OR "
                "positionCaseInsensitiveUTF8(ifNull(isin, ''), {search:String}) > 0)"
            )
            parameters["search"] = search
        if status:
            conditions.append("status = {status:String}")
            parameters["status"] = status
        if source:
            conditions.append("source = {source:String}")
            parameters["source"] = source
        if cursor:
            cursor_symbol, cursor_instrument = decode_instrument_cursor(cursor)
            conditions.append(
                "(trading_symbol, instrument_id) > "
                "({cursor_symbol:String}, {cursor_instrument:UUID})"
            )
            parameters.update(
                cursor_symbol=cursor_symbol,
                cursor_instrument=cursor_instrument,
            )

        result = self.client.query(
            f"""
            SELECT
                instrument_id, instrument_key, trading_symbol, name, isin,
                exchange_code, segment, instrument_type, asset_class, currency_code,
                lot_size, tick_size, status, source, first_seen, last_seen, ingested_at
            FROM ref_instruments FINAL
            WHERE {' AND '.join(conditions)}
            ORDER BY trading_symbol ASC, instrument_id ASC
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
                (SELECT uniqExact(instrument_id)
                 FROM ref_instruments FINAL
                 WHERE market_code = 'IND') AS reference_instruments,
                uniqExact(instrument_id) AS instruments_with_data,
                uniqExact(tuple(instrument_id, contract_id)) AS unique_series,
                count() AS data_points,
                uniqExact(toDate(bar_time, 'Asia/Kolkata')) AS trading_days,
                minOrNull(bar_time) AS first_bar_time,
                maxOrNull(bar_time) AS last_bar_time,
                maxOrNull(as_of_time) AS data_as_of
            FROM market_candles_1min FINAL
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
                toDate(bar_time, 'Asia/Kolkata') AS trading_date,
                count() AS data_points,
                uniqExact(instrument_id) AS unique_instruments,
                uniqExact(tuple(instrument_id, contract_id)) AS unique_series,
                min(bar_time) AS first_bar_time,
                max(bar_time) AS last_bar_time,
                max(as_of_time) AS data_as_of
            FROM market_candles_1min FINAL
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
    conditions = ["market_code = 'IND'"]
    parameters: dict[str, Any] = {}
    if date_from:
        conditions.append("toDate(bar_time, 'Asia/Kolkata') >= {date_from:Date}")
        parameters["date_from"] = date_from
    if date_to:
        conditions.append("toDate(bar_time, 'Asia/Kolkata') <= {date_to:Date}")
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
            "instrument": str(candle.instrument_id),
            "contract": str(candle.contract_id),
            "source": candle.source,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        return {
            "cursor_time": datetime.fromisoformat(payload["time"]),
            "cursor_symbol": str(payload["symbol"]),
            "cursor_instrument": UUID(payload["instrument"]),
            "cursor_contract": UUID(payload["contract"]),
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
            "instrument": str(instrument.instrument_id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_instrument_cursor(cursor: str) -> tuple[str, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        symbol = payload["symbol"]
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("invalid instrument symbol")
        return symbol, UUID(payload["instrument"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid instrument pagination cursor",
        ) from exc
