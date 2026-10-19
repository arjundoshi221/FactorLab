"""Cross-account trade ledger: every executed order with its theme, strategy and trade name.

Built from the same de-duplicated fills and latest tag assignments as the account order view
(``Book``), so the two always agree. Filters run over the profile's whole recorded history
before pagination; the existing 20-account and 20,000-fill report bounds apply.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from factorlab.components.api.portfolio import Repo
from factorlab.components.api.portfolio_pnl import TAG_FIELDS, number, timestamp
from factorlab.components.api.portfolio_tagging import Book

TagStatus = Literal["tagged", "untagged", "not_taggable"]
TagFilter = Literal["all", "tagged", "untagged", "not_taggable"]
MAX_ACCOUNTS = 20
MAX_FILLS = 20_000
FILLS_PER_ORDER = 50
CATALOG_KINDS = {"theme_id": "themes", "strategy_id": "strategies", "trade_name_id": "trade-names"}

router = APIRouter()


class LedgerRow(BaseModel):
    key: str
    broker_code: str
    account_id: str
    account_mode: str
    simulated: bool
    perm_id: str
    taggable: bool
    trading_symbol: str
    listing_id: str | None
    security_id: str | None
    contract_id: str | None
    product_type: str
    currency: str
    side: str
    quantity: str
    avg_price: str | None
    first_fill_at: datetime
    last_fill_at: datetime
    fill_count: int
    commissions: list[dict[str, str]]
    broker_realized_pnl: str | None
    theme_id: str | None
    theme_name: str | None
    strategy_id: str | None
    strategy_name: str | None
    trade_name_id: str | None
    trade_name_name: str | None
    tag_status: TagStatus
    fills: list[dict[str, Any]]
    fills_truncated: bool


class LedgerPage(BaseModel):
    items: list[LedgerRow]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]
    accounts: list[dict[str, Any]]
    generated_at: datetime


def text(value: Any) -> str | None:
    return None if value is None else str(value)


def build_orders(
    account: dict[str, Any], fills: list[dict[str, Any]], names: dict[str, dict[str, str]]
) -> list[LedgerRow]:
    """Group one account's fills into orders; quantities and prices stay exact decimals."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        perm = int(fill["perm_id"])
        # Missing perm_ids get an execution-specific key; never group unrelated zero IDs.
        grouped.setdefault(str(perm) if perm > 0 else f"fill:{fill['exec_id']}", []).append(fill)
    rows = []
    with localcontext() as context:
        context.prec = 64
        for key, items in grouped.items():
            items.sort(key=lambda f: (timestamp(f["exec_time"]), str(f["exec_id"])))
            first = items[0]
            perm = int(first["perm_id"])
            quantities = [number(f["quantity"]) for f in items]
            prices = [number(f["price"]) for f in items]
            quantity = sum((q for q in quantities if q is not None), Decimal(0))
            complete = all(q is not None for q in quantities) and all(p is not None for p in prices)
            notional = (
                sum((q * p for q, p in zip(quantities, prices, strict=True)), Decimal(0))  # type: ignore[operator]
                if complete
                else None
            )
            sides = sorted({str(f["side"]) for f in items})
            commissions: dict[str, Decimal] = {}
            for fill in items:
                amount = number(fill.get("commission"))
                if amount is not None:
                    currency = str(fill.get("commission_ccy") or "UNKNOWN")
                    commissions[currency] = commissions.get(currency, Decimal(0)) + amount
            realized = [number(f.get("realized_pnl")) for f in items]
            taggable = perm > 0 and bool(account.get("user_id"))
            tags = {field: text(first.get(field)) for field in TAG_FIELDS}
            status: TagStatus = (
                "not_taggable" if not taggable else "tagged" if tags["theme_id"] else "untagged"
            )
            rows.append(
                LedgerRow(
                    key=f"{account['broker_code']}:{account['account_id']}:{key}",
                    broker_code=str(account["broker_code"]),
                    account_id=str(account["account_id"]),
                    account_mode=str(
                        first.get("account_mode") or account.get("account_mode") or ""
                    ),
                    simulated=bool(account.get("simulated")),
                    perm_id=str(perm),
                    taggable=taggable,
                    trading_symbol=str(first.get("trading_symbol") or ""),
                    listing_id=text(first.get("listing_id")),
                    security_id=text(first.get("security_id")),
                    contract_id=text(first.get("contract_id")),
                    product_type=str(first.get("product_type") or ""),
                    currency=str(first.get("currency") or ""),
                    side=sides[0] if len(sides) == 1 else "MIXED",
                    quantity=str(quantity),
                    avg_price=str(notional / quantity)
                    if notional is not None and quantity
                    else None,
                    first_fill_at=timestamp(first["exec_time"]),
                    last_fill_at=timestamp(items[-1]["exec_time"]),
                    fill_count=len(items),
                    commissions=[
                        {"currency": c, "amount": str(a)} for c, a in sorted(commissions.items())
                    ],
                    # Broker-reported, in the order's own currency; any missing fill makes it unknown.
                    broker_realized_pnl=None
                    if any(r is None for r in realized)
                    else str(sum(realized, Decimal(0))),  # type: ignore[arg-type]
                    theme_id=tags["theme_id"],
                    theme_name=names["theme_id"].get(tags["theme_id"] or "")
                    if tags["theme_id"]
                    else None,
                    strategy_id=tags["strategy_id"],
                    strategy_name=names["strategy_id"].get(tags["strategy_id"] or "")
                    if tags["strategy_id"]
                    else None,
                    trade_name_id=tags["trade_name_id"],
                    trade_name_name=names["trade_name_id"].get(tags["trade_name_id"] or "")
                    if tags["trade_name_id"]
                    else None,
                    tag_status=status,
                    fills=[
                        {k: v for k, v in f.items() if k not in TAG_FIELDS}
                        for f in items[:FILLS_PER_ORDER]
                    ],
                    fills_truncated=len(items) > FILLS_PER_ORDER,
                )
            )
    return rows


def matches(
    row: LedgerRow,
    *,
    account: tuple[str, str] | None,
    listing: set[str] | None,
    contracts: set[str],
    symbol: str,
    tags: dict[str, str],
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if account and (row.broker_code, row.account_id) != account:
        return False
    if listing is not None and not (
        (
            row.contract_id is None
            and (
                row.listing_id in listing or (row.listing_id is None and row.security_id in listing)
            )
        )
        or (row.contract_id is not None and row.contract_id in contracts)
    ):
        return False
    if symbol and symbol.lower() not in row.trading_symbol.lower():
        return False
    for field, value in tags.items():
        if getattr(row, field) != value:
            return False
    if start and row.last_fill_at < start:
        return False
    return not (end and row.last_fill_at > end)


@router.get("/ledger", response_model=LedgerPage)
def ledger(
    repo: Repo,
    user_id: UUID,
    broker_code: Annotated[str, Query(max_length=50)] = "",
    account_id: Annotated[str, Query(max_length=100)] = "",
    listing_id: UUID | None = None,
    symbol: Annotated[str, Query(max_length=50)] = "",
    theme_id: UUID | None = None,
    strategy_id: UUID | None = None,
    trade_name_id: UUID | None = None,
    tag: TagFilter = "all",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=20_000)] = 0,
) -> LedgerPage:
    if bool(broker_code) != bool(account_id):
        raise HTTPException(422, "broker_code and account_id must be supplied together")
    for value in (start, end):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, "start and end must include a timezone")
    book = Book(repo)
    profile = book.profile(user_id)
    accounts = profile_accounts = repo.accounts(profile)
    if broker_code:
        accounts = [repo.account(broker_code, account_id, profile)]
    if len(accounts) > MAX_ACCOUNTS:
        raise HTTPException(422, "Select one account to list more than 20 accounts")
    names = {
        field: {row["id"]: row["name"] for row in book.catalog(kind, user_id)}  # type: ignore[arg-type]
        for field, kind in CATALOG_KINDS.items()
    }
    rows: list[LedgerRow] = []
    fill_count = 0
    for account in accounts:
        fills = book.fills(account, book.assignments(account))
        fill_count += len(fills)
        if fill_count > MAX_FILLS:
            raise HTTPException(
                422, "Recorded history exceeds the 20,000-fill limit; select one account"
            )
        rows.extend(build_orders(account, fills, names))
    listing: set[str] | None = None
    contracts: set[str] = set()
    if listing_id is not None:
        found = repo.query(
            "SELECT toString(security_id) AS security_id FROM ref.listings FINAL "
            "WHERE listing_id = {listing:UUID} LIMIT 1",
            {"listing": listing_id},
        )
        listing = {str(listing_id), *(r["security_id"] for r in found)}
        contracts = {
            r["contract_id"]
            for r in repo.query(
                "SELECT toString(contract_id) AS contract_id FROM ref.contracts FINAL "
                "WHERE underlying_listing_id = {listing:UUID}",
                {"listing": listing_id},
            )
        }
    tag_filters = {
        field: str(value)
        for field, value in (
            ("theme_id", theme_id),
            ("strategy_id", strategy_id),
            ("trade_name_id", trade_name_id),
        )
        if value is not None
    }
    selected = [
        row
        for row in rows
        if matches(
            row,
            account=(broker_code, account_id) if broker_code else None,
            listing=listing,
            contracts=contracts,
            symbol=symbol.strip(),
            tags=tag_filters,
            start=start,
            end=end,
        )
    ]
    # Chip counts reflect every filter except the tag status itself.
    counts: dict[str, int] = dict.fromkeys(("tagged", "untagged", "not_taggable"), 0)
    for row in selected:
        counts[row.tag_status] += 1
    if tag != "all":
        selected = [row for row in selected if row.tag_status == tag]
    selected.sort(key=lambda row: (row.last_fill_at, row.key), reverse=True)
    return LedgerPage(
        items=selected[offset : offset + limit],
        total=len(selected),
        limit=limit,
        offset=offset,
        counts=counts,
        accounts=[
            {
                "broker_code": a["broker_code"],
                "account_id": a["account_id"],
                "account_mode": a.get("account_mode"),
                "simulated": bool(a.get("simulated")),
            }
            for a in profile_accounts
        ],
        generated_at=datetime.now(UTC),
    )
