"""Open-orders snapshotter.

Maps ib_async ``Trade`` -> ``OpenOrderSnapshot`` (rehaul §9.4). Observing
only — orders may have been placed by any client (typically the future
trade engine). Gateway must have "Download open orders on connection"
enabled to see cross-client orders.

This module is intentionally read-only: it inspects the Trade / Order
attributes returned by ``ib.openTrades()`` and never constructs an order.
The read-only-grep test asserts no ``Order`` subclass names appear.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from factorlab.sources.ibkr.client import mode_of, source_channel_of
from factorlab.sources.ibkr.portfolio import (
    DEFAULT_COUNTRY_CODE,
    _product_type,
    _to_decimal,
)
from factorlab.sources.ibkr.shapes import OpenOrderSnapshot, Side

if TYPE_CHECKING:
    from ib_async import IB

log = logging.getLogger(__name__)


def _normalize_side(raw: str | None) -> Side:
    if not raw:
        return "BUY"
    upper = raw.upper()
    if upper in ("BUY", "BOT"):
        return "BUY"
    if upper in ("SELL", "SLD"):
        return "SELL"
    if upper in ("SSHORT", "SS"):
        return "SSHORT"
    log.warning("Unexpected open-order side %r; defaulting to BUY", raw)
    return "BUY"


def snapshot_open_orders(
    ib: IB,
    *,
    snapshot_time: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
) -> list[OpenOrderSnapshot]:
    """Return one ``OpenOrderSnapshot`` per unfilled/working order."""
    now = snapshot_time if snapshot_time is not None else datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("snapshot_time must be UTC-aware")
    channel = source_channel_of(ib)
    mode = mode_of(ib)

    rows: list[OpenOrderSnapshot] = []
    for trade in ib.openTrades():
        # Trade groups .contract, .order (the parameters), .orderStatus (state).
        contract = trade.contract
        order = trade.order
        status = trade.orderStatus

        quantity = _to_decimal(getattr(order, "totalQuantity", None)) or Decimal(0)
        filled = _to_decimal(getattr(status, "filled", 0)) or Decimal(0)
        remaining = _to_decimal(getattr(status, "remaining", None))
        if remaining is None:
            remaining = quantity - filled

        rows.append(OpenOrderSnapshot(
            snapshot_time=now,
            broker_code="ibkr",
            account_id=getattr(order, "account", "") or "",
            account_mode=mode,
            country_code=country_code,
            perm_id=int(getattr(order, "permId", 0) or 0),
            order_id=int(getattr(order, "orderId", 0) or 0),
            placed_by_client=int(getattr(order, "clientId", 0) or 0) or None,
            listing_id=None,
            security_id=None,
            contract_id=None,
            entity_id=None,
            product_type=_product_type(contract.secType),
            vendor_id=str(contract.conId),
            trading_symbol=contract.symbol,
            currency=contract.currency or "",
            side=_normalize_side(getattr(order, "action", None)),
            order_type=(getattr(order, "orderType", "") or "").upper(),
            time_in_force=(getattr(order, "tif", "") or "").upper(),
            quantity=quantity,
            filled_quantity=filled,
            remaining_quantity=remaining,
            limit_price=_to_decimal(getattr(order, "lmtPrice", None)),
            aux_price=_to_decimal(getattr(order, "auxPrice", None)),
            status=getattr(status, "status", "") or "",
            resolution_confidence="unresolved",
            source="ibkr",
            source_channel=channel,
            as_of_time=now,
            ingested_at=now,
        ))
    log.info("Snapshotted %d open orders [mode=%s]", len(rows), mode)
    return rows
