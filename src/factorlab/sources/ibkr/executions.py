"""Executions puller.

Maps ib_async ``Fill`` -> ``ExecutionRecord`` (rehaul §9.3). ``exec_id`` is
IBKR's immutable key — deduplication is the storage layer's job (we always
overlap the pull window). ``reqExecutions`` defaults to today only; pass
``since`` to widen via ``ExecutionFilter(time=...)``.
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
from factorlab.sources.ibkr.shapes import ExecutionRecord, Side

if TYPE_CHECKING:
    from ib_async import IB

log = logging.getLogger(__name__)

_IBKR_TIME_FMT = "%Y%m%d %H:%M:%S"


def _normalize_side(raw: str | None) -> Side:
    if not raw:
        return "BUY"
    upper = raw.upper()
    if upper in ("BOT", "BUY"):
        return "BUY"
    if upper in ("SLD", "SELL"):
        return "SELL"
    if upper in ("SSHORT", "SS", "SSHORTX"):
        return "SSHORT"
    # IBKR sometimes returns other side codes for options; keep BUY/SELL/SSHORT
    # enum stable — anything else we didn't anticipate becomes BUY (audit later).
    log.warning("Unexpected IBKR side code %r; defaulting to BUY", raw)
    return "BUY"


def _normalize_exec_time(value: object) -> datetime:
    """Coerce IBKR's execution.time to a UTC-aware ``datetime``.

    ib_async exposes it as ``datetime`` most of the time; the raw wire form
    is ``"YYYYMMDD  HH:MM:SS TZ"``. Naive datetimes are assumed to already be
    UTC (ib_async 2.x parses to UTC).
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        # Strip any trailing tz name (ib_async normalizes but be defensive)
        cleaned = value.strip()
        for candidate in (cleaned, cleaned.split("  ")[0]):
            try:
                parsed = datetime.strptime(candidate, _IBKR_TIME_FMT)
                return parsed.replace(tzinfo=UTC)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(cleaned)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(f"Cannot parse IBKR exec time {value!r}") from exc
    raise TypeError(f"Unsupported exec time type: {type(value)!r}")


def pull_executions(
    ib: IB,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
) -> list[ExecutionRecord]:
    """Return one ``ExecutionRecord`` per fill.

    ``since``: lower bound (UTC-aware). ``None`` = IBKR's default (today).
    ``now``: injected clock for ``as_of_time`` / ``ingested_at`` (tests).
    """
    stamp = now if now is not None else datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError("now must be UTC-aware")
    channel = source_channel_of(ib)
    mode = mode_of(ib)

    if since is None:
        fills = ib.reqExecutions()
    else:
        if since.tzinfo is None:
            raise ValueError("since must be UTC-aware")
        from ib_async import ExecutionFilter
        filt = ExecutionFilter(time=since.strftime(_IBKR_TIME_FMT))
        fills = ib.reqExecutions(filt)

    rows: list[ExecutionRecord] = []
    for fill in fills:
        exe = fill.execution
        contract = fill.contract
        commission_report = getattr(fill, "commissionReport", None)
        commission = _to_decimal(getattr(commission_report, "commission", None)) if commission_report else None
        commission_ccy = (getattr(commission_report, "currency", "") or "") if commission_report else ""
        realized_pnl = _to_decimal(getattr(commission_report, "realizedPNL", None)) if commission_report else None

        rows.append(ExecutionRecord(
            exec_id=exe.execId,
            broker_code="ibkr",
            account_id=exe.acctNumber,
            account_mode=mode,
            country_code=country_code,
            order_id=int(exe.orderId),
            perm_id=int(exe.permId),
            placed_by_client=int(exe.clientId) if getattr(exe, "clientId", None) is not None else None,
            listing_id=None,
            security_id=None,
            contract_id=None,
            entity_id=None,
            product_type=_product_type(contract.secType),
            vendor_id=str(contract.conId),
            trading_symbol=contract.symbol,
            currency=contract.currency or "",
            exec_time=_normalize_exec_time(exe.time),
            side=_normalize_side(exe.side),
            quantity=_to_decimal(exe.shares) or Decimal(0),
            price=_to_decimal(exe.price) or Decimal(0),
            exchange=exe.exchange or "",
            liquidity_flag=_liquidity_flag(getattr(exe, "lastLiquidity", 0)),
            commission=commission,
            commission_ccy=commission_ccy,
            realized_pnl=realized_pnl,
            resolution_confidence="unresolved",
            source="ibkr",
            source_channel=channel,
            as_of_time=stamp,
            ingested_at=stamp,
        ))
    log.info("Pulled %d executions [mode=%s since=%s]", len(rows), mode, since)
    return rows


# IBKR lastLiquidity: 0 = None, 1 = Added, 2 = Removed, 3 = Liquidity Routed Out, 4 = Auction
_LIQUIDITY_MAP = {0: "", 1: "ADDED", 2: "REMOVED", 3: "ROUTED", 4: "AUCTION"}


def _liquidity_flag(code: int | None) -> str:
    return _LIQUIDITY_MAP.get(int(code) if code is not None else 0, "")
