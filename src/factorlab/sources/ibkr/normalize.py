"""Pure normalization: decoded IBKR capture payloads -> ``broker.*`` shapes.

Every function here is deterministic over its input: the same archived bytes
always produce the same rows. No IBKR connection, clock or storage access.

IBKR "unset" sentinels (``UNSET_DOUBLE`` = float max, ``UNSET_DECIMAL`` =
2**127-1) and non-finite numbers become ``None`` so they never reach a
``Decimal`` column.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from factorlab.sources.ibkr.capture import CapturedPayload, CaptureKind, decode_capture
from factorlab.sources.ibkr.errors import IBKRCaptureError
from factorlab.sources.ibkr.shapes import (
    SEC_TYPE_TO_PRODUCT,
    AccountStateRow,
    ExecutionRecord,
    OpenOrderSnapshot,
    PositionSnapshot,
    ProductType,
    Side,
)

log = logging.getLogger(__name__)

# Both IBKR accounts are booked in the US today; overridable per call.
DEFAULT_COUNTRY_CODE = "US"
BROKER_CODE = "ibkr"

_SENTINEL_ABS = Decimal("1e30")
_SEGMENTS = frozenset({"S", "C", "P"})  # securities / commodities / Paxos crypto
_LEGACY_EXEC_TIME_FMT = "%Y%m%d %H:%M:%S"
# IBKR lastLiquidity: 0 = None, 1 = Added, 2 = Removed, 3 = Liquidity Routed Out, 4 = Auction
_LIQUIDITY_MAP = {0: "", 1: "ADDED", 2: "REMOVED", 3: "ROUTED", 4: "AUCTION"}
_SIDE_ALIASES: dict[str, Side] = {
    "BOT": "BUY", "BUY": "BUY",
    "SLD": "SELL", "SELL": "SELL",
    "SSHORT": "SSHORT", "SS": "SSHORT", "SSHORTX": "SSHORT",
}


def to_decimal(value: object) -> Decimal | None:
    """Coerce an IBKR number to ``Decimal``; sentinels, blanks and non-finite values -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or abs(number) >= _SENTINEL_ABS:
        return None
    return number


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _optional_text(value: object) -> str | None:
    text = _text(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _records(payload: CapturedPayload, kind: CaptureKind) -> Iterable[Mapping[str, Any]]:
    if payload.kind != kind:
        raise ValueError(f"expected a {kind} capture, got {payload.kind}")
    for index, record in enumerate(payload.data):
        if not isinstance(record, Mapping):
            raise IBKRCaptureError(f"{kind} record {index} is not an object")
        yield record


def _mapping(record: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = record.get(name)
    return value if isinstance(value, Mapping) else {}


def product_type(sec_type: object) -> ProductType:
    if not sec_type:
        return "other"
    return SEC_TYPE_TO_PRODUCT.get(str(sec_type).upper(), "other")


def normalize_side(raw: str | None) -> Side:
    if not raw:
        return "BUY"
    side = _SIDE_ALIASES.get(raw.upper())
    if side is None:
        # Keep the BUY/SELL/SSHORT enum stable; unexpected codes are logged for audit.
        log.warning("Unexpected IBKR side code %r; defaulting to BUY", raw)
        return "BUY"
    return side


def normalize_exec_time(value: object) -> datetime:
    """Coerce IBKR's execution time (ISO string, wire string or datetime) to UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        cleaned = value.strip()
        try:
            parsed = datetime.fromisoformat(cleaned)
            return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
        # Legacy wire form "YYYYMMDD  HH:MM:SS [TZ]"; ib_async 2.x reports UTC.
        for candidate in (cleaned, cleaned.split("  ")[0], " ".join(cleaned.split()[:2])):
            try:
                return datetime.strptime(candidate, _LEGACY_EXEC_TIME_FMT).replace(tzinfo=UTC)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse IBKR exec time {value!r}")
    raise TypeError(f"Unsupported exec time type: {type(value)!r}")


def liquidity_flag(code: object) -> str:
    return _LIQUIDITY_MAP.get(_optional_int(code) or 0, "")


def normalize_positions(payload: CapturedPayload, *,
                        country_code: str = DEFAULT_COUNTRY_CODE) -> list[PositionSnapshot]:
    """One row per open position; zero lots (closed positions IBKR still emits) are dropped."""
    rows: list[PositionSnapshot] = []
    for item in _records(payload, "portfolio"):
        position = to_decimal(item.get("position"))
        if position is None or position == 0:
            continue
        contract = _mapping(item, "contract")
        rows.append(PositionSnapshot(
            snapshot_time=payload.fetched_at,
            broker_code=BROKER_CODE,
            account_id=_text(item.get("account")),
            account_mode=payload.mode,
            country_code=country_code,
            product_type=product_type(contract.get("secType")),
            vendor_id=_text(contract.get("conId")),
            trading_symbol=_text(contract.get("symbol")),
            currency=_text(contract.get("currency")),
            position=position,
            avg_cost=to_decimal(item.get("averageCost")),
            market_price=to_decimal(item.get("marketPrice")),
            market_value=to_decimal(item.get("marketValue")),
            unrealized_pnl=to_decimal(item.get("unrealizedPNL")),
            realized_pnl_ytd=to_decimal(item.get("realizedPNL")),
            market_value_usd=None,  # portfolio() does not FX-normalize
        ))
    return rows


def normalize_account_state(payload: CapturedPayload, *,
                            country_code: str = DEFAULT_COUNTRY_CODE,
                            metrics: set[str] | None = None) -> list[AccountStateRow]:
    """One row per (metric, segment, currency); ``metrics`` whitelists base tags.

    Segments come from a segment suffix (``-S``/``-C``/``-P``), which is
    stripped from ``metric``. Any other hyphenated tag is kept whole: IBKR's
    per-currency ledger tags (``$LEDGER-CashBalance``, ...) are distinct metrics.
    """
    rows: list[AccountStateRow] = []
    for value in _records(payload, "account_values"):
        tag = _text(value.get("tag"))
        base_tag, _, suffix = tag.rpartition("-")
        if suffix in _SEGMENTS and base_tag:
            metric, segment = base_tag, suffix
        else:
            metric, segment = tag, ""
        if metrics is not None and metric not in metrics:
            continue
        raw = value.get("value")
        number = to_decimal(raw)
        rows.append(AccountStateRow(
            snapshot_time=payload.fetched_at,
            broker_code=BROKER_CODE,
            account_id=_text(value.get("account")),
            account_mode=payload.mode,
            country_code=country_code,
            metric=metric,
            segment=segment,
            currency=_text(value.get("currency")) or "NONE",
            value_num=number,
            value_str=None if number is not None else _text(raw),
        ))
    return rows


def normalize_executions(payload: CapturedPayload, *,
                         country_code: str = DEFAULT_COUNTRY_CODE) -> list[ExecutionRecord]:
    """One row per fill. Duplicates are kept; ``exec_id`` dedupes in storage."""
    rows: list[ExecutionRecord] = []
    for fill in _records(payload, "executions"):
        execution = _mapping(fill, "execution")
        contract = _mapping(fill, "contract")
        report = _mapping(fill, "commissionReport")
        rows.append(ExecutionRecord(
            exec_id=_text(execution.get("execId")),
            broker_code=BROKER_CODE,
            account_id=_text(execution.get("acctNumber")),
            account_mode=payload.mode,
            country_code=country_code,
            order_id=_optional_int(execution.get("orderId")) or 0,
            perm_id=_optional_int(execution.get("permId")) or 0,
            placed_by_client=_optional_int(execution.get("clientId")),
            order_ref=_optional_text(execution.get("orderRef")),
            route_pref="",
            product_type=product_type(contract.get("secType")),
            vendor_id=_text(contract.get("conId")),
            trading_symbol=_text(contract.get("symbol")),
            currency=_text(contract.get("currency")),
            exec_time=normalize_exec_time(execution.get("time")),
            side=normalize_side(execution.get("side")),
            quantity=to_decimal(execution.get("shares")) or Decimal(0),
            price=to_decimal(execution.get("price")) or Decimal(0),
            exchange=_text(execution.get("exchange")),
            liquidity_flag=liquidity_flag(execution.get("lastLiquidity")),
            commission=to_decimal(report.get("commission")),
            commission_ccy=_text(report.get("currency")),
            realized_pnl=to_decimal(report.get("realizedPNL")),
        ))
    return rows


def normalize_open_orders(payload: CapturedPayload, *,
                          country_code: str = DEFAULT_COUNTRY_CODE) -> list[OpenOrderSnapshot]:
    """One row per working order, placed by any client (observe only)."""
    rows: list[OpenOrderSnapshot] = []
    for trade in _records(payload, "open_orders"):
        contract = _mapping(trade, "contract")
        order = _mapping(trade, "order")
        status = _mapping(trade, "orderStatus")
        quantity = to_decimal(order.get("totalQuantity")) or Decimal(0)
        filled = to_decimal(status.get("filled")) or Decimal(0)
        remaining = to_decimal(status.get("remaining"))
        rows.append(OpenOrderSnapshot(
            snapshot_time=payload.fetched_at,
            broker_code=BROKER_CODE,
            account_id=_text(order.get("account")),
            account_mode=payload.mode,
            country_code=country_code,
            perm_id=_optional_int(order.get("permId")) or 0,
            order_id=_optional_int(order.get("orderId")) or 0,
            placed_by_client=_optional_int(order.get("clientId")),
            order_ref=_optional_text(order.get("orderRef")),
            product_type=product_type(contract.get("secType")),
            vendor_id=_text(contract.get("conId")),
            trading_symbol=_text(contract.get("symbol")),
            currency=_text(contract.get("currency")),
            side=normalize_side(order.get("action")),
            order_type=_text(order.get("orderType")).upper(),
            time_in_force=_text(order.get("tif")).upper(),
            quantity=quantity,
            filled_quantity=filled,
            remaining_quantity=remaining if remaining is not None else quantity - filled,
            limit_price=to_decimal(order.get("lmtPrice")),
            aux_price=to_decimal(order.get("auxPrice")),
            status=_text(status.get("status")),
        ))
    return rows


NORMALIZERS: dict[CaptureKind, Callable[..., list[Any]]] = {
    "portfolio": normalize_positions,
    "account_values": normalize_account_state,
    "executions": normalize_executions,
    "open_orders": normalize_open_orders,
}


def normalize_capture(body: bytes, *, country_code: str = DEFAULT_COUNTRY_CODE) -> list[Any]:
    """Replay helper: archived ``raw.archive`` bytes -> the rows they produced."""
    payload = decode_capture(body)
    return NORMALIZERS[payload.kind](payload, country_code=country_code)
