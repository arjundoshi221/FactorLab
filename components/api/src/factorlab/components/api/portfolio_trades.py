"""Open and closed trades: each position's life from flat to flat, per account and instrument.

A trade opens with the first fill on a flat account/instrument/currency position and closes
when the position returns to flat; a fill that crosses zero closes one trade and opens the
next in the other direction, splitting its commission by quantity. Realized PnL is FIFO
within the trade, so it agrees with the tagged PnL engine whenever opening and closing orders
carry the same tags. Built from the same de-duplicated fills, marks and history
reconciliation as ``portfolio_pnl``; missing data is unavailable, never zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from factorlab.components.api.portfolio import Repo
from factorlab.components.api.portfolio_attributes import (
    INSTRUMENT_KEYS,
    label_rows,
    parse_bucket,
    parse_dims,
    pivot_groups,
)
from factorlab.components.api.portfolio_ledger import CATALOG_KINDS, MAX_ACCOUNTS, MAX_FILLS
from factorlab.components.api.portfolio_pnl import (
    FX_CONVERSIONS,
    SUPPORTED,
    TAG_FIELDS,
    identity,
    instrument,
    latest_fills,
    number,
    timestamp,
    unreconciled,
)
from factorlab.components.api.portfolio_tagging import Book
from factorlab.components.api.source_health import broker_snapshot_stale

TradeStatus = Literal["open", "closed"]
StatusFilter = Literal["all", "open", "closed"]
FILLS_PER_TRADE = 50
ZERO = Decimal(0)
SIDES = ("BUY", "SELL", "SSHORT")
RETURN_STEP = Decimal("0.000001")
# Any of these makes a trade's PnL unknowable; the trade itself is still listed.
FATAL = {
    "incomplete_history",
    "invalid_fill",
    "unsupported_instrument",
    "missing_instrument",
    "missing_currency",
}

router = APIRouter()
INSTRUMENT_FIELDS = set(INSTRUMENT_KEYS)


class TradeRow(BaseModel):
    key: str
    broker_code: str
    account_id: str
    account_mode: str
    simulated: bool
    trading_symbol: str
    listing_id: str | None
    security_id: str | None
    contract_id: str | None
    vendor_id: str | None = None
    product_type: str
    currency: str
    direction: Literal["long", "short"]
    status: TradeStatus
    opened_at: datetime
    closed_at: datetime | None
    last_fill_at: datetime
    quantity: str
    max_quantity: str
    entry_quantity: str
    exit_quantity: str
    avg_entry_price: str
    avg_exit_price: str | None
    open_cost: str
    market_price: str | None
    price_time: datetime | None
    realized: str | None
    unrealized: str | None
    commissions: list[dict[str, str]]
    net: str | None
    return_pct: str | None
    theme_id: str | None
    theme_name: str | None
    strategy_id: str | None
    strategy_name: str | None
    trade_name_id: str | None
    trade_name_name: str | None
    tags_mixed: bool
    orders: list[str]
    fill_count: int
    fills: list[dict[str, Any]]
    fills_truncated: bool
    issues: list[str]
    # Pivot labels (``dims=sector``), e.g. ``{"sector": {"id": "45", "label": "..."}}``.
    attributes: dict[str, dict[str, Any]] = {}


class TradeSummary(BaseModel):
    currency: str
    account_mode: str
    simulated: bool
    closed: int
    valued: int
    wins: int
    losses: int
    realized: str
    net: str | None


class OpenSummary(BaseModel):
    currency: str
    account_mode: str
    simulated: bool
    open: int
    # Σ unrealized of the open trades; null if any of them is unavailable.
    unrealized: str | None


class TradeBucket(BaseModel):
    """One pivot bucket (e.g. a sector): status counts and results, never across currencies."""

    id: str | None
    label: str
    counts: dict[str, int]
    summary: list[TradeSummary]
    open: list[OpenSummary]


class TradesPage(BaseModel):
    items: list[TradeRow]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]
    summary: list[TradeSummary]
    accounts: list[dict[str, Any]]
    generated_at: datetime
    # ``dims=sector``: buckets over every filtered trade (all statuses, ignoring ``bucket``).
    pivots: dict[str, list[TradeBucket]] = {}
    unavailable_dimensions: list[str] = []


def text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def build_trades(
    executions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    snapshot_times: dict[tuple[str, str], datetime | None],
    names: dict[str, dict[str, str]],
    *,
    now: datetime | None = None,
) -> list[TradeRow]:
    """Every open and closed trade in ``executions``; quantities and prices stay exact decimals."""
    with localcontext() as context:
        context.prec = 64
        return _build(executions, snapshots, snapshot_times, names, now or datetime.now(UTC))


def _valid(row: dict[str, Any]) -> bool:
    quantity, price = number(row.get("quantity")), number(row.get("price"))
    return (
        quantity is not None
        and quantity > 0
        and price is not None
        and price >= 0
        and row.get("side") in SIDES
    )


def _build(
    executions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    snapshot_times: dict[tuple[str, str], datetime | None],
    names: dict[str, dict[str, str]],
    now: datetime,
) -> list[TradeRow]:
    executions = [
        row for row in latest_fills(executions) if row.get("product_type") not in FX_CONVERSIONS
    ]
    marks = {
        identity(row): row for row in snapshots if row.get("product_type") not in FX_CONVERSIONS
    }
    incomplete = unreconciled(executions, marks, snapshot_times)
    invalid = {identity(row) for row in executions if not _valid(row)}
    current: dict[tuple[str, ...], dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []

    def start(row: dict[str, Any], key: tuple[str, ...], signed: Decimal) -> dict[str, Any]:
        trade = {
            "row": row,
            "key": key,
            "direction": "long" if signed > 0 else "short",
            "opened_at": timestamp(row["exec_time"]),
            "closed_at": None,
            "last_fill_at": timestamp(row["exec_time"]),
            "position": ZERO,
            "max_quantity": ZERO,
            "entry_quantity": ZERO,
            "entry_notional": ZERO,
            "exit_quantity": ZERO,
            "exit_notional": ZERO,
            "realized": ZERO,
            "lots": [],
            "commissions": {},
            "issues": set(),
            "tags": [],
            "orders": [],
            "fills": [],
        }
        trades.append(trade)
        current[key] = trade
        return trade

    def record(
        trade: dict[str, Any], row: dict[str, Any], part: Decimal, total: Decimal, role: str
    ) -> None:
        trade["last_fill_at"] = timestamp(row["exec_time"])
        tags = tuple(text(row.get(field)) for field in TAG_FIELDS)
        if tags not in trade["tags"]:
            trade["tags"].append(tags)
        perm = str(row.get("perm_id", 0))
        if perm not in trade["orders"]:
            trade["orders"].append(perm)
        commission = number(row.get("commission"))
        if commission == 0 and not row.get("commission_ccy"):
            commission = None  # Gateway fill before IBKR's commission report: unknown, not zero.
        share = None if commission is None else commission * part / total
        if share is None:
            trade["issues"].add("missing_commission")
        else:
            currency = str(row.get("commission_ccy") or "UNKNOWN")
            trade["commissions"][currency] = trade["commissions"].get(currency, ZERO) + share
        trade["fills"].append(
            {
                "exec_id": str(row["exec_id"]),
                "exec_time": row["exec_time"],
                "perm_id": perm,
                "side": row["side"],
                "role": role,
                "quantity": str(part),
                "price": str(row["price"]),
                "commission": None if share is None else str(share),
                "commission_ccy": row.get("commission_ccy"),
            }
        )

    for row in executions:
        if not _valid(row):
            continue  # The instrument is flagged ``invalid_fill`` below; nothing is guessed.
        quantity, price = Decimal(str(row["quantity"])), Decimal(str(row["price"]))
        key = (*identity(row), str(row.get("currency") or "UNKNOWN"))
        signed = quantity if row["side"] == "BUY" else -quantity
        trade = current.get(key)
        if trade and (trade["position"] > 0) != (signed > 0):
            closing = min(abs(signed), abs(trade["position"]))
            direction = Decimal(1) if trade["position"] > 0 else Decimal(-1)
            left = closing
            lots: list[list[Decimal]] = trade["lots"]
            while left:
                lot = lots[0]
                matched = min(left, abs(lot[0]))
                trade["realized"] += matched * (price - lot[1]) * direction
                lot[0] -= matched * direction
                left -= matched
                if not lot[0]:
                    lots.pop(0)
            trade["position"] -= closing * direction
            trade["exit_quantity"] += closing
            trade["exit_notional"] += closing * price
            closed = not trade["position"]
            record(trade, row, closing, quantity, "close" if closed else "reduce")
            if closed:
                trade["closed_at"] = timestamp(row["exec_time"])
                del current[key]
            signed += closing * direction
        if signed:
            adding = trade is not None and key in current
            trade = current[key] if adding else start(row, key, signed)
            trade["lots"].append([signed, price])
            trade["position"] += signed
            trade["entry_quantity"] += abs(signed)
            trade["entry_notional"] += abs(signed) * price
            trade["max_quantity"] = max(trade["max_quantity"], abs(trade["position"]))
            record(trade, row, abs(signed), quantity, "add" if adding else "open")

    return [_row(trade, marks, incomplete, invalid, snapshot_times, names, now) for trade in trades]


def _row(
    trade: dict[str, Any],
    marks: dict[tuple[str, str, str], dict[str, Any]],
    incomplete: set[tuple[str, str, str]],
    invalid: set[tuple[str, str, str]],
    snapshot_times: dict[tuple[str, str], datetime | None],
    names: dict[str, dict[str, str]],
    now: datetime,
) -> TradeRow:
    row = trade["row"]
    ident = identity(row)
    issues: set[str] = trade["issues"]
    if ident in invalid:
        issues.add("invalid_fill")
    elif ident in incomplete:
        issues.add("incomplete_history")
    if not snapshot_times.get((row["broker_code"], row["account_id"])):
        issues.add("history_unverified")
    if row.get("product_type") not in SUPPORTED:
        issues.add("unsupported_instrument")
    if not instrument(row):
        issues.add("missing_instrument")
    if not row.get("currency"):
        issues.add("missing_currency")
    currency = str(row.get("currency") or "UNKNOWN")
    is_open = trade["closed_at"] is None
    open_cost = sum((abs(qty) * cost for qty, cost in trade["lots"]), ZERO)
    market_price: Decimal | None = None
    price_time: datetime | None = None
    unrealized: Decimal | None = ZERO
    if is_open:
        mark = marks.get(ident, {})
        market_price = number(mark.get("market_price"))
        if market_price is None or market_price < 0 or mark.get("currency") != currency:
            market_price, unrealized = None, None
            issues.add("missing_price")
        else:
            price_time = timestamp(mark["snapshot_time"])
            unrealized = sum((qty * (market_price - cost) for qty, cost in trade["lots"]), ZERO)
            if broker_snapshot_stale(price_time, now):
                issues.add("stale_price")
    fatal = bool(FATAL & issues)
    realized: Decimal | None = None if fatal else trade["realized"]
    if fatal:
        unrealized = None
    costs: dict[str, Decimal] = trade["commissions"]
    fees_known = "missing_commission" not in issues and set(costs) <= {currency}
    net = (
        realized + unrealized - costs.get(currency, ZERO)
        if realized is not None and unrealized is not None and fees_known
        else None
    )
    gross = None if realized is None or unrealized is None else realized + unrealized
    ret = (
        (gross / trade["entry_notional"]).quantize(RETURN_STEP)
        if gross is not None and trade["entry_notional"]
        else None
    )
    tags = trade["tags"][0]
    tag_values = dict(zip(TAG_FIELDS, tags, strict=True))
    fills = trade["fills"]
    return TradeRow(
        key=":".join(
            (
                str(row["broker_code"]),
                str(row["account_id"]),
                trade["key"][2],
                currency,
                trade["opened_at"].isoformat(),
                fills[0]["exec_id"],
            )
        ),
        broker_code=str(row["broker_code"]),
        account_id=str(row["account_id"]),
        account_mode=str(row.get("account_mode") or ""),
        simulated=bool(row.get("simulated")),
        trading_symbol=str(row.get("trading_symbol") or ""),
        listing_id=text(row.get("listing_id")),
        security_id=text(row.get("security_id")),
        contract_id=text(row.get("contract_id")),
        vendor_id=text(row.get("vendor_id")),
        product_type=str(row.get("product_type") or ""),
        currency=currency,
        direction=trade["direction"],
        status="open" if is_open else "closed",
        opened_at=trade["opened_at"],
        closed_at=trade["closed_at"],
        last_fill_at=trade["last_fill_at"],
        quantity=str(trade["position"]),
        max_quantity=str(trade["max_quantity"]),
        entry_quantity=str(trade["entry_quantity"]),
        exit_quantity=str(trade["exit_quantity"]),
        avg_entry_price=str(trade["entry_notional"] / trade["entry_quantity"]),
        avg_exit_price=str(trade["exit_notional"] / trade["exit_quantity"])
        if trade["exit_quantity"]
        else None,
        open_cost=str(open_cost),
        market_price=None if market_price is None else str(market_price),
        price_time=price_time,
        realized=None if realized is None else str(realized),
        unrealized=None if unrealized is None else str(unrealized),
        commissions=[
            {"currency": code, "amount": str(amount)} for code, amount in sorted(costs.items())
        ],
        net=None if net is None else str(net),
        return_pct=None if ret is None else str(ret),
        theme_id=tag_values["theme_id"],
        theme_name=names["theme_id"].get(tag_values["theme_id"] or ""),
        strategy_id=tag_values["strategy_id"],
        strategy_name=names["strategy_id"].get(tag_values["strategy_id"] or ""),
        trade_name_id=tag_values["trade_name_id"],
        trade_name_name=names["trade_name_id"].get(tag_values["trade_name_id"] or ""),
        tags_mixed=len(trade["tags"]) > 1,
        orders=trade["orders"],
        fill_count=len(fills),
        fills=fills[:FILLS_PER_TRADE],
        fills_truncated=len(fills) > FILLS_PER_TRADE,
        issues=sorted(issues),
    )


def summarize(rows: list[TradeRow]) -> list[TradeSummary]:
    """Closed-trade results per currency, account mode and simulation; never mixed."""
    groups: dict[tuple[str, str, bool], dict[str, Any]] = {}
    with localcontext() as context:
        context.prec = 64
        for row in rows:
            if row.status != "closed":
                continue
            item = groups.setdefault(
                (row.currency, row.account_mode, row.simulated),
                {"closed": 0, "valued": 0, "wins": 0, "losses": 0, "realized": ZERO, "net": ZERO},
            )
            item["closed"] += 1
            if row.realized is None:
                continue
            realized = Decimal(row.realized)
            item["valued"] += 1
            item["realized"] += realized
            item["wins"] += int(realized > 0)
            item["losses"] += int(realized < 0)
            item["net"] = (
                None if row.net is None or item["net"] is None else item["net"] + Decimal(row.net)
            )
    return [
        TradeSummary(
            currency=currency,
            account_mode=mode,
            simulated=simulated,
            closed=item["closed"],
            valued=item["valued"],
            wins=item["wins"],
            losses=item["losses"],
            realized=str(item["realized"]),
            net=None if item["net"] is None or not item["valued"] else str(item["net"]),
        )
        for (currency, mode, simulated), item in sorted(groups.items())
    ]


def summarize_open(rows: list[TradeRow]) -> list[OpenSummary]:
    """Open trades and their unrealized PnL per currency, account mode and simulation."""
    groups: dict[tuple[str, str, bool], list[TradeRow]] = {}
    for row in rows:
        if row.status == "open":
            groups.setdefault((row.currency, row.account_mode, row.simulated), []).append(row)
    out: list[OpenSummary] = []
    with localcontext() as context:
        context.prec = 64
        for (currency, mode, simulated), members in sorted(groups.items()):
            known = all(row.unrealized is not None for row in members)
            total = sum((Decimal(row.unrealized or 0) for row in members), ZERO)
            out.append(
                OpenSummary(
                    currency=currency,
                    account_mode=mode,
                    simulated=simulated,
                    open=len(members),
                    unrealized=str(total) if known else None,
                )
            )
    return out


def bucket_summaries(
    rows: list[TradeRow], attributes: list[dict[str, Any]], dim: str
) -> list[TradeBucket]:
    out: list[TradeBucket] = []
    for value, members in pivot_groups(rows, attributes, dim):
        counts = {"open": 0, "closed": 0}
        for row in members:
            counts[row.status] += 1
        out.append(
            TradeBucket(
                id=value["id"],
                label=str(value["label"]),
                counts=counts,
                summary=summarize(members),
                open=summarize_open(members),
            )
        )
    return out


@router.get("/trades", response_model=TradesPage)
def trades(
    repo: Repo,
    user_id: UUID,
    broker_code: Annotated[str, Query(max_length=50)] = "",
    account_id: Annotated[str, Query(max_length=100)] = "",
    symbol: Annotated[str, Query(max_length=50)] = "",
    theme_id: UUID | None = None,
    strategy_id: UUID | None = None,
    trade_name_id: UUID | None = None,
    status: StatusFilter = "all",
    start: datetime | None = None,
    end: datetime | None = None,
    dims: Annotated[str, Query(max_length=200)] = "",
    bucket: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=20_000)] = 0,
) -> TradesPage:
    """Trades active in [start, end], newest activity first; filters apply before paging.

    ``dims=sector`` labels each trade's instrument and adds ``pivots`` (per-sector counts
    and results over every filtered trade); ``bucket=sector:<id>`` (``sector:`` for
    Unclassified) then keeps one bucket. A failed lookup is named in
    ``unavailable_dimensions`` and its bucket filter is not applied.
    """
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
    fills: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    times: dict[tuple[str, str], datetime | None] = {}
    for account in accounts:
        fills.extend(book.fills(account, book.assignments(account)))
        if len(fills) > MAX_FILLS:
            raise HTTPException(
                422, "Recorded history exceeds the 20,000-fill limit; select one account"
            )
        snapshot = repo.snapshot(account, "positions")
        snapshots.extend({**row, "simulated": account["simulated"]} for row in snapshot.items)
        times[account["broker_code"], account["account_id"]] = snapshot.snapshot_time
    rows = build_trades(fills, snapshots, times, names)
    tags = {
        field: str(value)
        for field, value in (
            ("theme_id", theme_id),
            ("strategy_id", strategy_id),
            ("trade_name_id", trade_name_id),
        )
        if value is not None
    }
    needle = symbol.strip().lower()
    selected = [
        row
        for row in rows
        if (not needle or needle in row.trading_symbol.lower())
        and all(getattr(row, field) == value for field, value in tags.items())
        and not (end and row.opened_at > end)
        and not (start and row.closed_at is not None and row.closed_at < start)
    ]
    pivot_dims = parse_dims(dims)
    chosen = parse_bucket(bucket)
    if chosen and chosen[0] not in pivot_dims:
        pivot_dims.append(chosen[0])
    pivots: dict[str, list[TradeBucket]] = {}
    failed: list[str] = []
    if pivot_dims and selected:
        labels, failed = label_rows(
            repo, [row.model_dump(include=INSTRUMENT_FIELDS) for row in selected], pivot_dims
        )
        selected = [
            row.model_copy(update={"attributes": found})
            for row, found in zip(selected, labels, strict=True)
        ]
        pivots = {
            name: bucket_summaries(selected, labels, name)
            for name in pivot_dims
            if name not in failed
        }
        if chosen and chosen[0] not in failed:
            dim, wanted = chosen
            selected = [
                row for row in selected if (row.attributes.get(dim) or {}).get("id") == wanted
            ]
    # Status counts and the closed-trade summary ignore the status filter itself.
    counts = {"open": 0, "closed": 0}
    for row in selected:
        counts[row.status] += 1
    summary = summarize(selected)
    if status != "all":
        selected = [row for row in selected if row.status == status]
    selected.sort(key=lambda row: (row.last_fill_at, row.key), reverse=True)
    return TradesPage(
        items=selected[offset : offset + limit],
        total=len(selected),
        limit=limit,
        offset=offset,
        counts=counts,
        summary=summary,
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
        pivots=pivots,
        unavailable_dimensions=failed,
    )
