"""Recorded-history FIFO PnL, isolated from broker-reported account cost basis."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

ZERO = Decimal(0)
TAG_FIELDS = ("theme_id", "strategy_id", "trade_name_id")
SUPPORTED = {"STK", "ETF", "stock", "equity", "etf", "common", "preferred", "adr", "reit"}


def number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def timestamp(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result


def instrument(row: dict[str, Any]) -> str:
    # Vendor identity also works before canonical security resolution completes.
    for key in ("vendor_id", "contract_id", "listing_id"):
        value = str(row.get(key) or "")
        if value and value != "00000000-0000-0000-0000-000000000000":
            return f"{key}:{value}"
    return ""


def identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["broker_code"]), str(row["account_id"]), instrument(row)


def latest_fills(executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in executions:
        key = str(row["broker_code"]), str(row["account_id"]), str(row["exec_id"])
        if key not in found or int(row.get("version", 0)) > int(found[key].get("version", 0)):
            found[key] = row
    return sorted(
        found.values(), key=lambda row: (timestamp(row["exec_time"]), str(row["exec_id"]))
    )


def ledger(
    executions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    snapshot_times: dict[tuple[str, str], datetime | None],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """One row per account/instrument/tag triple/currency; never silently truncate fills."""
    with localcontext() as context:
        context.prec = 64  # Decimal(20,6) products and sums must retain their fractional digits.
        return _ledger(executions, snapshots, snapshot_times, now=now)


def _ledger(
    executions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    snapshot_times: dict[tuple[str, str], datetime | None],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    executions = latest_fills(executions)
    marks = {identity(row): row for row in snapshots}
    expected: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    invalid: set[tuple[str, str, str]] = set()
    for row in executions:
        cutoff = snapshot_times.get((row["broker_code"], row["account_id"]))
        quantity = number(row.get("quantity"))
        if quantity is None or quantity <= 0 or row.get("side") not in ("BUY", "SELL", "SSHORT"):
            invalid.add(identity(row))
        elif cutoff and timestamp(row["exec_time"]) <= cutoff:
            expected[identity(row)] += quantity if row["side"] == "BUY" else -quantity
    for key in set(expected) | set(marks):
        actual = number(marks.get(key, {}).get("position", 0))
        if actual is None or actual != expected[key]:
            invalid.add(key)

    buckets: dict[tuple[Any, ...], dict[str, Any]] = {}
    lots: dict[tuple[Any, ...], deque[list[Decimal]]] = defaultdict(deque)

    def bucket(row: dict[str, Any], currency: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
        tags = tuple(str(row.get(field)) if row.get(field) else None for field in TAG_FIELDS)
        key = (*identity(row), *tags, currency)
        if key not in buckets:
            buckets[key] = {
                "broker_code": row["broker_code"],
                "account_id": row["account_id"],
                "account_mode": row["account_mode"],
                "simulated": row.get("simulated", False),
                "instrument": instrument(row),
                "trading_symbol": row.get("trading_symbol", ""),
                **dict(zip(TAG_FIELDS, tags, strict=True)),
                "currency": currency,
                "realized": ZERO,
                "unrealized": ZERO,
                "commissions": ZERO,
                "quantity": ZERO,
                "orders": set(),
                "issues": set(),
                "price_time": None,
                "history_start": row["exec_time"],
            }
        return key, buckets[key]

    for row in executions:
        key, item = bucket(row, str(row.get("currency") or "UNKNOWN"))
        item["orders"].add(str(row.get("perm_id", 0)))
        if not all(row.get(field) for field in TAG_FIELDS):
            item["issues"].add("unassigned")
        if not instrument(row):
            item["issues"].add("missing_instrument")
        if not row.get("currency"):
            item["issues"].add("missing_currency")
        if identity(row) in invalid:
            item["issues"].add("incomplete_history")
        if not snapshot_times.get((row["broker_code"], row["account_id"])):
            item["issues"].add("history_unverified")
        price, quantity = number(row.get("price")), number(row.get("quantity"))
        if quantity is not None and quantity > 0 and row.get("side") in ("BUY", "SELL", "SSHORT"):
            item["quantity"] += quantity if row["side"] == "BUY" else -quantity
        if row.get("product_type") not in SUPPORTED:
            item["issues"].add("unsupported_instrument")
        elif (
            price is None
            or price < 0
            or quantity is None
            or quantity <= 0
            or row.get("side") not in ("BUY", "SELL", "SSHORT")
        ):
            item["issues"].add("invalid_fill")
        else:
            signed = quantity if row["side"] == "BUY" else -quantity
            queue = lots[key]
            while signed and queue and (queue[0][0] > 0) != (signed > 0):
                old_quantity, old_price = queue[0]
                matched = min(abs(signed), abs(old_quantity))
                direction = Decimal(1) if old_quantity > 0 else Decimal(-1)
                item["realized"] += matched * (price - old_price) * direction
                queue[0][0] -= matched * direction
                signed += matched * direction
                if not queue[0][0]:
                    queue.popleft()
            if signed:
                queue.append([signed, price])
        commission = number(row.get("commission"))
        if commission is None:
            item["issues"].add("missing_commission")
        else:
            commission_currency = str(row.get("commission_ccy") or "UNKNOWN")
            _, costs = bucket(row, commission_currency)
            costs["commissions"] += commission
            costs["orders"].add(str(row.get("perm_id", 0)))
            if commission_currency == "UNKNOWN":
                item["issues"].add("missing_commission_currency")
                costs["issues"].add("missing_commission_currency")

    result: list[dict[str, Any]] = []
    fatal = {
        "incomplete_history",
        "missing_instrument",
        "invalid_fill",
        "unsupported_instrument",
        "missing_currency",
    }
    for key, item in buckets.items():
        queue = lots[key]
        if queue:
            mark = marks.get(key[:3], {})
            price = number(mark.get("market_price"))
            if price is None or price < 0 or mark.get("currency") != item["currency"]:
                item["issues"].add("missing_price")
            else:
                item["unrealized"] = sum((qty * (price - cost) for qty, cost in queue), ZERO)
                item["price_time"] = mark["snapshot_time"]
                if (now - timestamp(mark["snapshot_time"])).total_seconds() > 7200:
                    item["issues"].add("stale_price")
        realized = None if fatal & item["issues"] else item["realized"]
        unrealized = (
            None
            if fatal & item["issues"] or "missing_price" in item["issues"]
            else item["unrealized"]
        )
        costs = None if "missing_commission" in item["issues"] else item["commissions"]
        total = (
            None
            if realized is None
            or unrealized is None
            or costs is None
            or "missing_commission_currency" in item["issues"]
            else realized + unrealized - costs
        )
        result.append(
            {
                **item,
                "realized": None if realized is None else str(realized),
                "unrealized": None if unrealized is None else str(unrealized),
                "commissions": None if costs is None else str(costs),
                "total": None if total is None else str(total),
                "quantity": str(item["quantity"]),
                "orders": sorted(item["orders"]),
                "issues": sorted(item["issues"]),
            }
        )
    seen = {identity(row) for row in executions}
    for key, row in marks.items():
        if key in seen or not number(row.get("position")):
            continue
        result.append(
            {
                "broker_code": row["broker_code"],
                "account_id": row["account_id"],
                "account_mode": row["account_mode"],
                "simulated": row.get("simulated", False),
                "instrument": instrument(row),
                "trading_symbol": row.get("trading_symbol", ""),
                **dict.fromkeys(TAG_FIELDS),
                "currency": row["currency"],
                "realized": None,
                "unrealized": None,
                "commissions": None,
                "total": None,
                "quantity": str(row["position"]),
                "orders": [],
                "issues": ["incomplete_history", "unassigned"],
                "price_time": row["snapshot_time"],
                "history_start": row["snapshot_time"],
            }
        )
    return result


def summarize(details: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """Each dimension uses the same ledger; modes, simulation and currencies stay separate."""
    with localcontext() as context:
        context.prec = 64
        return _summarize(details, group_by)


def _summarize(details: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in details:
        key = row[group_by], row["account_mode"], row["simulated"], row["currency"]
        item = grouped.setdefault(
            key,
            {
                "id": row[group_by],
                "account_mode": row["account_mode"],
                "simulated": row["simulated"],
                "currency": row["currency"],
                "realized": ZERO,
                "unrealized": ZERO,
                "commissions": ZERO,
                "total": ZERO,
                "issues": set(),
                "details": [],
            },
        )
        for field in ("realized", "unrealized", "commissions", "total"):
            item[field] = (
                None
                if item[field] is None or row[field] is None
                else item[field] + Decimal(row[field])
            )
        item["issues"].update(row["issues"])
        item["details"].append(row)
    return [
        {
            **item,
            **{
                field: None if item[field] is None else str(item[field])
                for field in ("realized", "unrealized", "commissions", "total")
            },
            "issues": sorted(item["issues"]),
        }
        for item in grouped.values()
    ]
