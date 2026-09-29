"""Position and account-state snapshots (capture + normalize, no archive).

Convenience wrappers for research and diagnostics. Production ingestion goes
through :class:`factorlab.sources.ibkr.provider.IBKRBrokerProvider`, which
archives each capture before normalizing it. Uses ``ib.portfolio()`` (not
``ib.positions()``) to pick up market price, value and PnL on every row.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from factorlab.sources.ibkr.capture import capture_account_values, capture_portfolio, decode_capture
from factorlab.sources.ibkr.normalize import (
    DEFAULT_COUNTRY_CODE,
    normalize_account_state,
    normalize_positions,
)
from factorlab.sources.ibkr.shapes import AccountStateRow, PositionSnapshot

if TYPE_CHECKING:
    from ib_async import IB


def snapshot_positions(
    ib: IB,
    *,
    snapshot_time: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
) -> list[PositionSnapshot]:
    """Return one ``PositionSnapshot`` per open position across managed accounts."""
    capture = capture_portfolio(ib, fetched_at=snapshot_time)
    return normalize_positions(decode_capture(capture.body), country_code=country_code)


def snapshot_account_state(
    ib: IB,
    *,
    snapshot_time: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
    metrics: set[str] | None = None,
) -> list[AccountStateRow]:
    """Return one ``AccountStateRow`` per (metric, segment, currency) tuple."""
    capture = capture_account_values(ib, fetched_at=snapshot_time)
    return normalize_account_state(decode_capture(capture.body),
                                   country_code=country_code, metrics=metrics)
