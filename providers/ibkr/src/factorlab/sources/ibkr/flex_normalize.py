"""Pure normalization: archived Flex statement XML -> ``broker.*`` shapes.

Deterministic over the archived bytes (no clock, network or storage), like
:mod:`factorlab.sources.ibkr.normalize`. One Activity Flex statement can hold
several accounts (``<FlexStatement accountId=...>``); each yields:

* ``<Trade levelOfDetail="EXECUTION">`` -> :class:`ExecutionRecord` (one per fill);
* ``levelOfDetail="ORDER"`` rows -> :class:`OrderEventRecord`, one terminal event
  per executed order. Flex reports only what executed, so orders that never
  filled are absent, and the event sits at ``ORDER_SUMMARY_EVENT_SEQ`` so it
  never collides with streamed lifecycle events (0..n) for the same ``perm_id``;
* ``<CashTransaction>`` -> :class:`CashFlowRecord` (``SUMMARY`` rows are skipped).

Conventions that differ from the Gateway API, mapped here:

* ``dateTime`` is US/Eastern wall time (``yyyyMMdd;HHmmss`` by default; any
  separator works) and becomes UTC;
* ``ibCommission`` is a signed cash amount (cost < 0); ``commission`` keeps the
  Gateway sign (cost > 0);
* ``quantity`` is signed (sell < 0); records carry the absolute size and ``side``;
* ``ibOrderID`` is IBKR's durable order id, i.e. the Gateway's ``permId``. Flex
  has no session ``orderId`` (0) nor API ``clientId``: ``isAPIOrder="N"`` maps to
  client 0 (manual), anything else to ``None`` (API client unknown);
* the account mode comes from the account id (IBKR paper accounts start with ``D``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from defusedxml import ElementTree

from factorlab.sources.ibkr.errors import IBKRCaptureError
from factorlab.sources.ibkr.normalize import (
    BROKER_CODE,
    DEFAULT_COUNTRY_CODE,
    normalize_side,
    product_type,
    to_decimal,
)
from factorlab.sources.ibkr.shapes import (
    CashFlowRecord,
    ExecutionRecord,
    Mode,
    OrderEventRecord,
)

log = logging.getLogger(__name__)

FLEX_TIMEZONE = ZoneInfo("America/New_York")
ORDER_SUMMARY_EVENT_SEQ = 2**32 - 1  # UInt32 max: after any streamed event of the order

_CASH_FLOW_TYPES = {
    "dividends": "dividend",
    "payment in lieu of dividends": "dividend",
    "withholding tax": "tax_withholding",
    "871(m) withholding": "tax_withholding",
    "broker interest received": "interest",
    "broker interest paid": "interest",
    "bond interest received": "interest",
    "bond interest paid": "interest",
    "other fees": "fee",
    "broker fees": "fee",
    "commission adjustments": "commission",
}
_DEPOSITS = frozenset({"deposits/withdrawals", "deposits & withdrawals"})
_DIVIDEND_TYPES = frozenset({"dividends", "payment in lieu of dividends"})


def flex_time(value: str) -> datetime:
    """Parse a Flex date or date-time (US/Eastern wall time) to UTC."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 8:
        digits += "000000"
    elif len(digits) == 12:
        digits += "00"
    if len(digits) != 14:
        raise ValueError(f"Cannot parse IBKR Flex time {value!r}")
    local = datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=FLEX_TIMEZONE)
    return local.astimezone(UTC)


def _flex_date(value: str | None) -> date | None:
    digits = re.sub(r"\D", "", value or "")
    return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])) if len(digits) == 8 else None


def mode_for_account(account_id: str) -> Mode:
    return "paper" if account_id.upper().startswith("D") else "live"


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _abs(value: str | None) -> Decimal | None:
    number = to_decimal(value)
    return None if number is None else abs(number)


def _client(row: Any) -> int | None:
    return 0 if row.get("isAPIOrder", "").upper() == "N" else None


def _statements(body: bytes) -> Iterator[Any]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise IBKRCaptureError(f"IBKR Flex capture is not valid XML: {exc}") from exc
    if root.tag != "FlexQueryResponse":
        raise IBKRCaptureError(f"Unsupported IBKR Flex document <{root.tag}>")
    yield from root.iter("FlexStatement")


def _execution(row: Any, account: str, mode: Mode, country: str) -> ExecutionRecord:
    commission = to_decimal(row.get("ibCommission"))
    return ExecutionRecord(
        exec_id=row.get("ibExecID", ""),
        broker_code=BROKER_CODE,
        account_id=account,
        account_mode=mode,
        country_code=country,
        order_id=0,
        perm_id=_int(row.get("ibOrderID")),
        placed_by_client=_client(row),
        order_ref=row.get("orderReference") or None,
        route_pref="",
        product_type=product_type(row.get("assetCategory")),
        vendor_id=row.get("conid", ""),
        trading_symbol=row.get("symbol", ""),
        currency=row.get("currency", ""),
        exec_time=flex_time(row.get("dateTime", "")),
        side=normalize_side(row.get("buySell")),
        quantity=_abs(row.get("quantity")) or Decimal(0),
        price=to_decimal(row.get("tradePrice")) or Decimal(0),
        exchange=row.get("exchange", ""),
        liquidity_flag="",
        commission=None if commission is None else -commission,
        commission_ccy=row.get("ibCommissionCurrency", ""),
        realized_pnl=to_decimal(row.get("fifoPnlRealized")),
    )


def _order_event(row: Any, account: str, mode: Mode, country: str) -> OrderEventRecord:
    filled = _abs(row.get("quantity"))
    return OrderEventRecord(
        event_time=flex_time(row.get("dateTime", "")),
        broker_code=BROKER_CODE,
        account_id=account,
        account_mode=mode,
        country_code=country,
        perm_id=_int(row.get("ibOrderID")),
        order_id=0,
        event_seq=ORDER_SUMMARY_EVENT_SEQ,
        event_type="filled",
        prior_state=None,
        new_state="Filled",
        placed_by_client=_client(row),
        order_ref=row.get("orderReference") or None,
        product_type=product_type(row.get("assetCategory")),
        vendor_id=row.get("conid", ""),
        trading_symbol=row.get("symbol", ""),
        currency=row.get("currency", ""),
        quantity_after=filled,
        filled_qty_at_event=filled,
        avg_fill_price_at_event=to_decimal(row.get("tradePrice")),
        remaining_at_event=Decimal(0),
    )


def _cash_flow(row: Any, account: str, mode: Mode, country: str) -> CashFlowRecord | None:
    amount = to_decimal(row.get("amount"))
    if amount is None:
        return None
    kind = row.get("type", "").strip().lower()
    if kind in _DEPOSITS:
        flow = "deposit" if amount >= 0 else "withdrawal"
    else:
        flow = _CASH_FLOW_TYPES.get(kind, "other")
    return CashFlowRecord(
        event_time=flex_time(row.get("dateTime") or row.get("reportDate", "")),
        broker_code=BROKER_CODE,
        account_id=account,
        account_mode=mode,
        country_code=country,
        cash_flow_type=flow,
        amount=amount,
        currency=row.get("currency", ""),
        vendor_id=row.get("conid", ""),
        description=row.get("description", ""),
        ex_date=_flex_date(row.get("exDate")),
        pay_date=_flex_date(row.get("settleDate")) if kind in _DIVIDEND_TYPES else None,
    )


def normalize_flex_statement(
    body: bytes, *, country_code: str = DEFAULT_COUNTRY_CODE
) -> list[ExecutionRecord | OrderEventRecord | CashFlowRecord]:
    """Every execution, executed order and cash movement in an Activity Flex statement."""
    rows: list[ExecutionRecord | OrderEventRecord | CashFlowRecord] = []
    for statement in _statements(body):
        account = statement.get("accountId", "")
        mode = mode_for_account(account)
        for trades in statement.iter("Trades"):
            for row in trades:
                if "(Ca.)" in row.get("buySell", ""):
                    continue  # IBKR's cancellation leg of a busted trade; not a fill
                level = row.get("levelOfDetail", "").upper()
                if level == "EXECUTION" and row.get("ibExecID"):
                    rows.append(
                        _execution(row, row.get("accountId") or account, mode, country_code)
                    )
                elif level == "ORDER" and row.get("ibOrderID"):
                    rows.append(
                        _order_event(row, row.get("accountId") or account, mode, country_code)
                    )
        for cash in statement.iter("CashTransaction"):
            if cash.get("levelOfDetail", "").upper() == "SUMMARY":
                continue
            record = _cash_flow(cash, cash.get("accountId") or account, mode, country_code)
            if record is None:
                log.warning("Skipping IBKR Flex cash transaction without an amount")
                continue
            rows.append(record)
    return rows


__all__ = [
    "FLEX_TIMEZONE",
    "ORDER_SUMMARY_EVENT_SEQ",
    "flex_time",
    "mode_for_account",
    "normalize_flex_statement",
]
