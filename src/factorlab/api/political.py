"""Read models and ClickHouse queries for political trades."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict

from factorlab.storage.clickhouse import ClickHouseStorage


class PoliticalTrade(BaseModel):
    """One latest-version congressional transaction disclosure."""

    model_config = ConfigDict(extra="forbid")

    trade_key: str
    political_trade_id: UUID
    listing_id: UUID | None
    contract_id: UUID | None
    legislator_entity_id: UUID | None
    chamber: str
    filing_id: str
    filing_date: date
    filing_url: str
    bioguide_id: str | None
    legislator_name: str
    state: str
    district: int | None
    owner_code: str
    filer_type: str
    asset_name_raw: str
    ticker: str | None
    asset_type_code: str
    transaction_type: str
    transaction_date: date
    notification_date: date | None
    amount_str: str
    amount_min: int | None
    amount_max: int | None
    source: str
    as_of_time: datetime
    ingested_at: datetime


class PoliticalTradesPage(BaseModel):
    """A cursor-paginated political-trades response."""

    items: list[PoliticalTrade]
    next_cursor: str | None
    limit: int
    data_as_of: datetime | None


class QueryResult(Protocol):
    column_names: tuple[str, ...] | list[str]
    result_rows: list[tuple[Any, ...]]


class QueryClient(Protocol):
    def query(self, query: str, parameters: dict[str, Any] | None = None) -> QueryResult: ...


class PoliticalTradesRepository:
    """Execute stable, parameterized reads against ``alt_political_trades``."""

    def __init__(self, client: QueryClient) -> None:
        self.client = client

    @classmethod
    def from_environment(cls) -> PoliticalTradesRepository:
        return cls(ClickHouseStorage.from_environment().client)

    def list_trades(
        self,
        *,
        ticker: str | None = None,
        bioguide_id: str | None = None,
        chamber: Literal["house", "senate"] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> PoliticalTradesPage:
        conditions: list[str] = []
        parameters: dict[str, Any] = {"fetch_limit": limit + 1}

        if ticker:
            conditions.append("t.ticker_raw = {ticker:Nullable(String)}")
            parameters["ticker"] = ticker.upper()
        if bioguide_id:
            conditions.append("t.bioguide_id = {bioguide_id:Nullable(String)}")
            parameters["bioguide_id"] = bioguide_id.upper()
        if chamber:
            conditions.append("t.chamber = {chamber:String}")
            parameters["chamber"] = chamber
        if date_from:
            conditions.append("t.transaction_date >= {date_from:Date}")
            parameters["date_from"] = date_from
        if date_to:
            conditions.append("t.transaction_date <= {date_to:Date}")
            parameters["date_to"] = date_to
        if cursor:
            cursor_date, cursor_key = decode_cursor(cursor)
            conditions.append(
                "(t.transaction_date < {cursor_date:Date} OR "
                "(t.transaction_date = {cursor_date:Date} AND t.political_trade_id < {cursor_key:UUID}))"
            )
            parameters.update(cursor_date=cursor_date, cursor_key=cursor_key)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        result = self.client.query(
            f"""
            SELECT
                toString(political_trade_id) AS trade_key, political_trade_id,
                listing_id, contract_id, legislator_entity_id,
                chamber, filing_id, filing_date, filing_url,
                bioguide_id, legislator_name_raw AS legislator_name,
                ifNull(terms.state, '') AS state, terms.district, owner_code,
                filer_type, asset_name_raw, ticker_raw AS ticker,
                filing_asset_type_code AS asset_type_code,
                transaction_type, transaction_date, notification_date,
                amount_str_raw AS amount_str, amount_min, amount_max,
                source, as_of_time, ingested_at
            FROM alt.political_trades AS t FINAL
            LEFT JOIN (
                SELECT bioguide_id, argMax(state, term_start) AS state,
                       argMax(district, term_start) AS district
                FROM ref.legislator_terms FINAL GROUP BY bioguide_id
            ) AS terms ON t.bioguide_id = terms.bioguide_id
            {where}
            ORDER BY transaction_date DESC, political_trade_id DESC
            LIMIT {{fetch_limit:UInt16}}
            """,
            parameters=parameters,
        )
        rows = [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [PoliticalTrade.model_validate(row) for row in rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(last.transaction_date, str(last.political_trade_id))
        return PoliticalTradesPage(
            items=items,
            next_cursor=next_cursor,
            limit=limit,
            data_as_of=max((item.as_of_time for item in items), default=None),
        )


def encode_cursor(transaction_date: date, political_trade_id: str) -> str:
    payload = json.dumps(
        {"v": 2, "date": transaction_date.isoformat(), "political_trade_id": political_trade_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[date, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(payload, dict) or payload.get("v") != 2:
            raise HTTPException(400, "Legacy cursor; restart pagination with canonical political trade IDs")
        cursor_date = date.fromisoformat(payload["date"])
        political_trade_id = UUID(payload["political_trade_id"])
        return cursor_date, political_trade_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor",
        ) from exc
