"""Position and account-state snapshotters.

Maps ib_async ``PortfolioItem`` -> ``PositionSnapshot`` (rehaul §9.1)
and ``AccountValue`` -> ``AccountStateRow`` (rehaul §9.2). Uses
``ib.portfolio()`` (not ``ib.positions()``) because we want ``marketPrice``,
``marketValue``, ``unrealizedPNL``, ``realizedPNL`` on every row.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from factorlab.sources.ibkr.client import mode_of, source_channel_of
from factorlab.sources.ibkr.shapes import (
    SEC_TYPE_TO_PRODUCT,
    AccountStateRow,
    PositionSnapshot,
    ProductType,
)

if TYPE_CHECKING:
    from ib_async import IB

log = logging.getLogger(__name__)

# Default country code for IBKR accounts today (both DUE375963 paper and
# U18065781 live are booked in the US). Overridable per call.
DEFAULT_COUNTRY_CODE = "US"


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _product_type(sec_type: str | None) -> ProductType:
    if not sec_type:
        return "other"
    return SEC_TYPE_TO_PRODUCT.get(sec_type.upper(), "other")


def snapshot_positions(
    ib: IB,
    *,
    snapshot_time: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
) -> list[PositionSnapshot]:
    """Return one ``PositionSnapshot`` per open position across managed accounts.

    Uses ``ib.portfolio()`` to pick up market prices and PnL. Positions with
    ``position == 0`` are dropped (IBKR occasionally emits closed lots).
    """
    now = snapshot_time if snapshot_time is not None else datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("snapshot_time must be UTC-aware")
    channel = source_channel_of(ib)
    mode = mode_of(ib)

    rows: list[PositionSnapshot] = []
    for item in ib.portfolio():
        pos = _to_decimal(item.position)
        if pos is None or pos == 0:
            continue
        contract = item.contract
        rows.append(PositionSnapshot(
            snapshot_time=now,
            broker_code="ibkr",
            account_id=item.account,
            account_mode=mode,
            country_code=country_code,
            listing_id=None,
            security_id=None,
            contract_id=None,
            entity_id=None,
            product_type=_product_type(contract.secType),
            vendor_id=str(contract.conId),
            trading_symbol=contract.symbol,
            currency=contract.currency or "",
            position=pos,
            avg_cost=_to_decimal(item.averageCost),
            market_price=_to_decimal(item.marketPrice),
            market_value=_to_decimal(item.marketValue),
            unrealized_pnl=_to_decimal(item.unrealizedPNL),
            realized_pnl_ytd=_to_decimal(item.realizedPNL),
            market_value_usd=None,  # IBKR portfolio() does not FX-normalize; leave nullable
            resolution_confidence="unresolved",
            source="ibkr",
            source_channel=channel,
            as_of_time=now,
            ingested_at=now,
        ))
    log.info("Snapshotted %d positions [mode=%s]", len(rows), mode)
    return rows


def snapshot_account_state(
    ib: IB,
    *,
    snapshot_time: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
    metrics: set[str] | None = None,
) -> list[AccountStateRow]:
    """Return one ``AccountStateRow`` per (metric, segment, currency) tuple.

    ``metrics`` filters to a base-tag whitelist (e.g. ``{'NetLiquidation',
    'BuyingPower'}``); ``None`` keeps everything (~144 rows per account).
    Segments come from tag suffix (``-S``/``-C``/``-P``); base tag is what
    goes into ``metric``.
    """
    now = snapshot_time if snapshot_time is not None else datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("snapshot_time must be UTC-aware")
    channel = source_channel_of(ib)
    mode = mode_of(ib)

    rows: list[AccountStateRow] = []
    for v in ib.accountValues():
        base_tag, _, suffix = v.tag.partition("-")
        segment = suffix if suffix in ("S", "C", "P") else ""
        if metrics is not None and base_tag not in metrics:
            continue
        try:
            value_num: Decimal | None = Decimal(str(v.value))
            value_str: str | None = None
        except (ArithmeticError, ValueError):
            value_num = None
            value_str = v.value
        rows.append(AccountStateRow(
            snapshot_time=now,
            broker_code="ibkr",
            account_id=v.account,
            account_mode=mode,
            country_code=country_code,
            metric=base_tag,
            segment=segment,
            currency=v.currency or "NONE",
            value_num=value_num,
            value_str=value_str,
            source="ibkr",
            source_channel=channel,
            as_of_time=now,
            ingested_at=now,
        ))
    log.info("Snapshotted %d account-state rows [mode=%s]", len(rows), mode)
    return rows
