"""IBKR-reported PnL beside the tagged FIFO ledger, and per-instrument reconciliation.

IBKR's figures come from Flex statements only, so their basis is consistent: each fill's
``fifoPnlRealized`` (net of the commissions of the lots it closes) and each open position's
FIFO ``costBasisMoney`` (which includes opening commissions), revalued at the same mark the
ledger uses. IBKR total = realized + unrealized; no fees are subtracted again.

IBKR matches lots across the whole account, not per tag, so its realized PnL for an
instrument held under several tags can land on a different tag than the tagged FIFO. The
per-instrument total (realized + unrealized) does not depend on lot matching, which is what
the reconciliation compares.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time
from decimal import Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from .portfolio_pnl import (
    FX_CONVERSIONS,
    SUPPORTED,
    TAG_FIELDS,
    ZERO,
    identity,
    instrument,
    latest_fills,
    number,
    timestamp,
)

# Flex open positions are as of the report date's New York close.
STATEMENT_CLOSE = time(16)
NEW_YORK = ZoneInfo("America/New_York")
STOCKS = {"STK"}
NO_TAGS: tuple[str | None, ...] = (None, None, None)
# Rounding slack per fill: Flex reports commissions and PnL to a few decimals.
TOLERANCE_PER_FILL = Decimal("0.01")

Key = tuple[Any, ...]


def statement(header: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One account's latest Flex open positions, keyed like the ledger's instruments.

    ``header`` is the statement's own row (generation time and report date); an account
    with no open positions has a header and no position rows.
    """
    if header is None:
        return None
    statement_to = header["statement_to"]
    if not isinstance(statement_to, date):
        statement_to = date.fromisoformat(str(statement_to))
    positions: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("asset_category") or "") not in STOCKS or not row.get("vendor_id"):
            continue
        key = f"vendor_id:{row['vendor_id']}"
        level = str(row.get("level_of_detail") or "SUMMARY").upper()
        item = positions.setdefault(
            key,
            {
                "quantity": None,
                "cost_basis": None,
                "currency": "",
                "trading_symbol": "",
                "lots": [],
            },
        )
        if level == "SUMMARY":
            item["quantity"] = number(row.get("position"))
            item["cost_basis"] = number(row.get("cost_basis"))
            item["currency"] = str(row.get("currency") or "")
            item["trading_symbol"] = str(row.get("trading_symbol") or "")
        elif level == "LOT":
            item["lots"].append(
                {
                    "quantity": number(row.get("position")),
                    "cost_basis": number(row.get("cost_basis")),
                    "perm_id": int(number(row.get("perm_id")) or 0),
                }
            )
    return {
        "generated_at": timestamp(header["generated_at"]),
        "statement_to": statement_to,
        "cutoff": datetime.combine(statement_to, STATEMENT_CLOSE, NEW_YORK).astimezone(UTC),
        "positions": {key: value for key, value in positions.items() if value["quantity"]},
    }


def detail_key(row: dict[str, Any]) -> Key:
    tags = tuple(row.get(field) for field in TAG_FIELDS)
    return (
        str(row["broker_code"]),
        str(row["account_id"]),
        row["instrument"],
        *tags,
        row["currency"],
    )


def fill_key(row: dict[str, Any]) -> Key:
    tags = tuple(str(row.get(field)) if row.get(field) else None for field in TAG_FIELDS)
    return (*identity(row), *tags, str(row.get("currency") or "UNKNOWN"))


def _fills(executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in latest_fills(executions)
        if row.get("product_type") not in FX_CONVERSIONS and instrument(row)
    ]


def _fill_realized(
    row: dict[str, Any], statements: dict[tuple[str, str], dict[str, Any] | None]
) -> tuple[Decimal | None, str | None]:
    """IBKR's net realized PnL for one fill, or why it is not available yet."""
    found = statements.get((str(row["broker_code"]), str(row["account_id"])))
    if found is None:
        return None, "ibkr_no_statement"
    if timestamp(row["exec_time"]) > found["cutoff"]:
        return None, "ibkr_awaiting_statement"
    realized = number(row.get("realized_pnl"))
    if realized is not None:
        return realized, None
    commission = number(row.get("commission"))
    if commission is None or (commission == 0 and not row.get("commission_ccy")):
        return None, "ibkr_missing_realized"
    # The Gateway reports no realized PnL for a fill that only opens a position.
    return ZERO, None


def _positions(
    fills: list[dict[str, Any]],
    statements: dict[tuple[str, str], dict[str, Any] | None],
    marks: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """IBKR's open-position value per account/instrument, at the ledger's mark."""
    traded: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in fills:
        if row.get("product_type") in SUPPORTED:  # IBKR positions are read for stocks/ETFs only
            traded[identity(row)].append(row)
    keys = set(traded) | {
        (broker, account, key)
        for (broker, account), found in statements.items()
        if found
        for key in found["positions"]
    }
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key in keys:
        broker, account, name = key
        found = statements.get((broker, account))
        issues: set[str] = set()
        value: Decimal | None = None
        lots: list[tuple[int, Decimal]] | None = None
        position = (found or {}).get("positions", {}).get(name) or {}
        quantity = position.get("quantity") or ZERO
        currency = position.get("currency") or next(
            (str(row.get("currency") or "") for row in traded.get(key, [])), ""
        )
        mark = marks.get(key, {})
        if found is None:
            issues.add("ibkr_no_statement")
        elif number(mark.get("position", 0)) != quantity or any(
            timestamp(row["exec_time"]) > found["cutoff"] for row in traded.get(key, [])
        ):
            issues.add("ibkr_awaiting_statement")
        elif not quantity:
            value, lots = ZERO, []
        else:
            price = number(mark.get("market_price"))
            basis = position.get("cost_basis")
            if price is None or price < 0 or mark.get("currency") != currency:
                issues.add("missing_price")
            elif basis is None:
                issues.add("ibkr_missing_basis")
            else:
                value = quantity * price - basis
                parts = position.get("lots") or []
                if (
                    parts
                    and all(lot["quantity"] and lot["cost_basis"] is not None for lot in parts)
                    and sum(lot["quantity"] for lot in parts) == quantity
                ):
                    lots = [
                        (lot["perm_id"], lot["quantity"] * price - lot["cost_basis"])
                        for lot in parts
                    ]
        result[key] = {
            "value": value,
            "lots": lots,
            "quantity": quantity,
            "currency": currency,
            "issues": issues,
            "trading_symbol": position.get("trading_symbol")
            or next((row.get("trading_symbol", "") for row in traded.get(key, [])), ""),
            "fills": traded.get(key, []),
        }
    return result


def with_broker_pnl(
    details: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    statements: dict[tuple[str, str], dict[str, Any] | None],
    marks: dict[tuple[str, str, str], dict[str, Any]],
    order_tags: dict[tuple[str, str, int], tuple[str | None, ...]],
    *,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Add ``ibkr_realized``/``ibkr_unrealized``/``ibkr_total``/``ibkr_issues`` per ledger row."""
    with localcontext() as context:
        context.prec = 64
        return _with_broker_pnl(details, executions, statements, marks, order_tags, since)


def _with_broker_pnl(
    details: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    statements: dict[tuple[str, str], dict[str, Any] | None],
    marks: dict[tuple[str, str, str], dict[str, Any]],
    order_tags: dict[tuple[str, str, int], tuple[str | None, ...]],
    since: datetime | None,
) -> list[dict[str, Any]]:
    fills = _fills(executions)
    realized: dict[Key, Decimal | None] = defaultdict(Decimal)
    issues: dict[Key, set[str]] = defaultdict(set)
    for row in fills:
        if since is not None and timestamp(row["exec_time"]) < since:
            continue
        key = fill_key(row)
        value, problem = _fill_realized(row, statements)
        if problem:
            issues[key].add(problem)
        current = realized[key]
        realized[key] = None if current is None or value is None else current + value

    unrealized: dict[Key, Decimal] = defaultdict(Decimal)
    blocked: dict[tuple[str, str, str], set[str]] = {}
    by_instrument: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        by_instrument[(str(row["broker_code"]), str(row["account_id"]), row["instrument"])].append(
            row
        )
    positions = _positions(fills, statements, marks)
    for key, position in positions.items():
        prefix, currency = key, position["currency"]
        if position["value"] is None:
            blocked[key] = position["issues"]
            continue
        if not position["quantity"]:
            continue
        if position["lots"] is not None and position["lots"]:
            for perm, value in position["lots"]:
                tags = order_tags.get((key[0], key[1], perm), NO_TAGS)
                unrealized[(*prefix, *tags, currency)] += value
            continue
        holders = {
            tuple(row[field] for field in TAG_FIELDS)
            for row in by_instrument.get(key, [])
            if row["currency"] == currency and number(row["quantity"])
        }
        held = sum(
            (
                number(row["quantity"]) or ZERO
                for row in by_instrument.get(key, [])
                if row["currency"] == currency
            ),
            ZERO,
        )
        if held != position["quantity"]:
            # Recorded history does not explain the position; fall back to its order tags.
            holders = {
                tuple(str(row.get(field)) if row.get(field) else None for field in TAG_FIELDS)
                for row in position["fills"]
            } or {NO_TAGS}
        if len(holders) == 1:
            unrealized[(*prefix, *holders.pop(), currency)] += position["value"]
        else:
            blocked[key] = {"ibkr_split_position"}

    seen: set[Key] = set()
    result = []
    for row in details:
        key = detail_key(row)
        seen.add(key)
        result.append(_annotate(row, key, realized, unrealized, issues, blocked, since))
    accounts = {(str(row["broker_code"]), str(row["account_id"])): row for row in fills}
    for key in (set(unrealized) | {key for key in realized if key not in seen}) - seen:
        # An IBKR lot from an order with no recorded fills, or fills the ledger skipped.
        source = accounts.get((key[0], key[1]), {})
        position = positions.get(key[:3], {})
        found = statements.get((key[0], key[1])) or {}
        row = {
            "broker_code": key[0],
            "account_id": key[1],
            "account_mode": source.get("account_mode", ""),
            "simulated": source.get("simulated", False),
            "instrument": key[2],
            "trading_symbol": position.get("trading_symbol", ""),
            **dict(zip(TAG_FIELDS, key[3:6], strict=True)),
            "currency": key[6],
            "realized": None,
            "unrealized": None,
            "commissions": None,
            "total": None,
            "quantity": "0",
            "orders": [],
            "issues": ["incomplete_history"],
            "price_time": None,
            "history_start": found.get("generated_at"),
        }
        result.append(_annotate(row, key, realized, unrealized, issues, blocked, since))
    return result


def _annotate(
    row: dict[str, Any],
    key: Key,
    realized: dict[Key, Decimal | None],
    unrealized: dict[Key, Decimal],
    issues: dict[Key, set[str]],
    blocked: dict[tuple[str, str, str], set[str]],
    since: datetime | None,
) -> dict[str, Any]:
    gain = realized.get(key, ZERO)
    problems = set(issues.get(key, set()))
    instrument_key = key[:3]
    if instrument_key in blocked:
        problems |= blocked[instrument_key]
        open_value: Decimal | None = None
    else:
        open_value = unrealized.get(key, ZERO)
    if since is not None:
        total = gain
    else:
        total = None if gain is None or open_value is None else gain + open_value
    return {
        **row,
        "ibkr_realized": None if gain is None else str(gain),
        "ibkr_unrealized": None if open_value is None else str(open_value),
        "ibkr_total": None if total is None else str(total),
        "ibkr_issues": sorted(problems),
    }


def reconcile(
    details: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    statements: dict[tuple[str, str], dict[str, Any] | None],
    marks: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """All-time tagged-FIFO total vs IBKR total per account/instrument; mismatches first."""
    with localcontext() as context:
        context.prec = 64
        return _reconcile(details, executions, statements, marks)


STATUS_ORDER = {"mismatch": 0, "ours_unavailable": 1, "ibkr_unavailable": 2, "not_comparable": 3}
# Issues that explain neither side's number; left out of the reconciliation reasons.
NOT_REASONS = {"unassigned", "stale_price", "history_unverified"}


def _reconcile(
    details: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    statements: dict[tuple[str, str], dict[str, Any] | None],
    marks: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    fills = _fills(executions)
    rows_by_instrument: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        rows_by_instrument[
            (str(row["broker_code"]), str(row["account_id"]), row["instrument"])
        ].append(row)
    accounts = {(str(row["broker_code"]), str(row["account_id"])): row for row in details}
    result = []
    for key, position in _positions(fills, statements, marks).items():
        rows = rows_by_instrument.get(key, [])
        currency = position["currency"]
        reasons: set[str] = set()
        ours = _sum(rows, "realized"), _sum(rows, "unrealized"), _sum(rows, "commissions")
        for row in rows:
            reasons |= set(row["issues"]) - NOT_REASONS
        status = ""
        if any(row["currency"] != currency for row in rows):
            status = "not_comparable"  # fees charged in another currency than the instrument
        gross, open_value, costs = ours
        ours_total = (
            None
            if gross is None or open_value is None or costs is None or not rows
            else gross + open_value - costs
        )
        ibkr_realized: Decimal | None = ZERO
        for row in position["fills"]:
            value, problem = _fill_realized(row, statements)
            if problem:
                reasons.add(problem)
            ibkr_realized = (
                None if ibkr_realized is None or value is None else ibkr_realized + value
            )
        reasons |= position["issues"]
        ibkr_total = (
            None
            if ibkr_realized is None or position["value"] is None
            else ibkr_realized + position["value"]
        )
        difference = None
        if not status:
            if ours_total is None:
                status = "ours_unavailable"
            elif ibkr_total is None:
                status = "ibkr_unavailable"
            else:
                difference = ours_total - ibkr_total
                tolerance = TOLERANCE_PER_FILL * (len(position["fills"]) + 1)
                status = "match" if abs(difference) <= tolerance else "mismatch"
        source = accounts.get((key[0], key[1]), {}) or next(iter(position["fills"]), {})
        result.append(
            {
                "broker_code": key[0],
                "account_id": key[1],
                "account_mode": source.get("account_mode", ""),
                "simulated": source.get("simulated", False),
                "trading_symbol": position["trading_symbol"],
                "currency": currency,
                "quantity": str(position["quantity"]),
                "fills": len(position["fills"]),
                "ours": {
                    "realized": _text(gross),
                    "unrealized": _text(open_value),
                    "commissions": _text(costs),
                    "total": _text(ours_total),
                },
                "ibkr": {
                    "realized": _text(ibkr_realized),
                    "unrealized": _text(position["value"]),
                    "total": _text(ibkr_total),
                },
                "difference": _text(difference),
                "status": status,
                "reasons": sorted(reasons),
            }
        )
    return sorted(
        result,
        key=lambda row: (
            STATUS_ORDER.get(row["status"], 9),
            -abs(number(row["difference"]) or ZERO),
            row["trading_symbol"],
            row["account_id"],
        ),
    )


def _sum(rows: list[dict[str, Any]], field: str) -> Decimal | None:
    total = ZERO
    for row in rows:
        value = number(row[field])
        if value is None:
            return None
        total += value
    return total


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def statement_meta(
    statements: dict[tuple[str, str], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    return [
        {
            "broker_code": broker,
            "account_id": account,
            "generated_at": None if found is None else found["generated_at"],
            "statement_to": None if found is None else found["statement_to"].isoformat(),
            "lot_detail": bool(found and any(p["lots"] for p in found["positions"].values())),
        }
        for (broker, account), found in sorted(statements.items())
    ]
