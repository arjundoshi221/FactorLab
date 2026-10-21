"""US futures curves (F-044), mounted under ``/hub/api/v1/us``.

One snapshot per root recorded by ``ingest-us futures`` (F-043): each live contract of
the root from ``ref.contracts`` with its latest quote from ``market.futures_quotes``.
A curve is the contracts in expiry order; nothing here rolls or back-adjusts prices.

``change`` is measured against the settlement the latest quote carries, which is the
previous session's; when a quote has none, the last daily bar's close before the
quote's session stands in.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from factorlab.calendars import globex
from factorlab.calendars.us import NY
from factorlab.clickhouse import ClickHouse, rows

SOURCE = "schwab"
ALIAS_KIND = "schwab_symbol"
QUOTE_WINDOW = timedelta(days=7)  # a weekend still shows Friday's curve
STALE_AFTER = timedelta(minutes=15)  # the recorder quotes every minute while Globex is open
ENERGY_ROOTS = frozenset({"/CL", "/NG", "/RB", "/HO", "/BZ"})

Group = Literal["index", "energy", "metals", "rates", "fx", "other"]


class Contract(BaseModel):
    symbol: str
    contract_id: str
    expiry: date
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    settlement_prev: float | None = None
    change: float | None = None
    change_pct: float | None = None
    day_volume: int | None = None
    oi: int | None = None
    observed_at: datetime | None = None
    stale: bool = False


class Root(BaseModel):
    root: str
    name: str
    product_type: str
    group: Group
    contracts: list[Contract]


class FuturesResponse(BaseModel):
    generated_at: datetime
    market_open: bool
    items: list[Root]


def group_of(root: str, product_type: str) -> Group:
    if product_type == "index_future":
        return "index"
    if product_type == "commodity_future":
        return "energy" if root in ENERGY_ROOTS else "metals"
    if product_type == "rate_future":
        return "rates"
    if product_type == "fx_future":
        return "fx"
    return "other"


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


class FuturesRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def query(self, sql: str, **parameters: Any) -> list[dict[str, Any]]:
        return rows(self.client.query(sql, parameters=parameters))

    def roots(self) -> list[dict[str, Any]]:
        return self.query(
            """SELECT a.alias_value AS root, a.target_id AS listing_id,
                e.legal_name AS name, s.security_type AS product_type
            FROM ref.identifier_aliases AS a FINAL
            INNER JOIN ref.listings AS l FINAL ON l.listing_id = a.target_id
            INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id
            LEFT JOIN ref.entities AS e FINAL ON e.entity_id = s.entity_id
            WHERE a.alias_kind = {kind:String} AND a.target_kind = 'listing'
              AND a.valid_to IS NULL AND startsWith(a.alias_value, '/')
              AND l.country_code = 'US' AND endsWith(s.security_type, '_future')
            ORDER BY root""",
            kind=ALIAS_KIND,
        )

    def contracts(self, listing_ids: list[Any], today: date) -> list[dict[str, Any]]:
        if not listing_ids:
            return []
        return self.query(
            """SELECT c.contract_id AS contract_id, c.underlying_listing_id AS listing_id,
                c.expiry AS expiry, a.alias_value AS symbol
            FROM ref.contracts AS c FINAL
            INNER JOIN (
                SELECT alias_value, target_id FROM ref.identifier_aliases FINAL
                WHERE alias_kind = {kind:String} AND target_kind = 'contract'
                  AND valid_to IS NULL
            ) AS a ON a.target_id = c.contract_id
            WHERE c.country_code = 'US' AND c.contract_type = 'future' AND c.active
              AND c.expiry >= {today:Date}
              AND c.underlying_listing_id IN {ids:Array(UUID)}
            ORDER BY listing_id, expiry""",
            kind=ALIAS_KIND,
            today=today,
            ids=listing_ids,
        )

    def latest_quotes(self, contract_ids: list[Any], since: datetime) -> dict[Any, dict[str, Any]]:
        if not contract_ids:
            return {}
        result = self.query(
            # Not "AS observed_at": the alias would shadow the column inside argMax
            # (ClickHouse ILLEGAL_AGGREGATION), as in the recorder's session-close query.
            """SELECT contract_id,
                argMax(last_price, observed_at) AS last_price,
                argMax(bid, observed_at) AS bid,
                argMax(ask, observed_at) AS ask,
                argMax(settlement_price, observed_at) AS settlement_price,
                argMax(day_volume, observed_at) AS day_volume,
                argMax(oi, observed_at) AS oi,
                argMax(trade_date, observed_at) AS trade_date,
                max(observed_at) AS last_observed_at
            FROM market.futures_quotes FINAL
            WHERE country_code = 'US' AND source = {source:String}
              AND observed_at >= {since:DateTime64(6)}
              AND contract_id IN {ids:Array(UUID)}
            GROUP BY contract_id""",
            source=SOURCE,
            since=since,
            ids=contract_ids,
        )
        return {row["contract_id"]: row for row in result}

    def last_closes(self, contract_ids: list[Any], before: date) -> dict[Any, Any]:
        if not contract_ids:
            return {}
        result = self.query(
            """SELECT contract_id, argMax(close, trade_date) AS close
            FROM market.futures_contract_bars FINAL
            WHERE country_code = 'US' AND source = {source:String} AND resolution = 'daily'
              AND trade_date < {before:Date} AND close IS NOT NULL
              AND contract_id IN {ids:Array(UUID)}
            GROUP BY contract_id""",
            source=SOURCE,
            before=before,
            ids=contract_ids,
        )
        return {row["contract_id"]: row["close"] for row in result}


def _contract(
    row: dict[str, Any], quote: dict[str, Any] | None, close: Any, stale_before: datetime | None
) -> Contract:
    contract = Contract(
        symbol=row["symbol"], contract_id=str(row["contract_id"]), expiry=row["expiry"]
    )
    if quote is None:
        contract.settlement_prev = _num(close)
        return contract
    last = _num(quote["last_price"])
    prev = _num(quote["settlement_price"])
    if prev is None:
        prev = _num(close)
    contract.last = last
    contract.bid = _num(quote["bid"])
    contract.ask = _num(quote["ask"])
    contract.settlement_prev = prev
    if last is not None and prev:
        contract.change = last - prev
        contract.change_pct = last / prev - 1.0
    contract.day_volume = _int(quote["day_volume"])
    contract.oi = _int(quote["oi"])
    observed = quote["last_observed_at"]
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    contract.observed_at = observed
    contract.stale = stale_before is not None and observed < stale_before
    return contract


def curves(repo: FuturesRepository, now: datetime) -> FuturesResponse:
    market_open = globex.is_open(now)
    stale_before = now - STALE_AFTER if market_open else None
    roots = repo.roots()
    contracts = repo.contracts([r["listing_id"] for r in roots], now.astimezone(NY).date())
    ids = [c["contract_id"] for c in contracts]
    quotes = repo.latest_quotes(ids, now - QUOTE_WINDOW)
    session = globex.session_date(now)
    closes = repo.last_closes(ids, session)
    by_root: dict[Any, list[Contract]] = {r["listing_id"]: [] for r in roots}
    for row in contracts:
        cid = row["contract_id"]
        by_root.setdefault(row["listing_id"], []).append(
            _contract(row, quotes.get(cid), closes.get(cid), stale_before)
        )
    items = [
        Root(
            root=r["root"],
            name=r["name"] or r["root"],
            product_type=r["product_type"],
            group=group_of(r["root"], r["product_type"]),
            contracts=by_root[r["listing_id"]],
        )
        for r in roots
    ]
    return FuturesResponse(generated_at=now, market_open=market_open, items=items)


def repository() -> FuturesRepository:
    """Request-local client: clickhouse-connect sessions are not thread-safe."""
    return FuturesRepository(ClickHouse.from_environment().client)


Repo = Annotated[FuturesRepository, Depends(repository)]
router = APIRouter()


@router.get("/futures", response_model=FuturesResponse)
def futures(repo: Repo) -> FuturesResponse:
    return curves(repo, datetime.now(UTC))
