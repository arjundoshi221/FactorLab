"""Open-orders snapshot (capture + normalize, no archive).

Observing only — orders may have been placed by any client (typically the
future trade engine). Each capture explicitly requests all clients' current
open orders with a bounded timeout; no startup order cache is required.

This module is intentionally read-only: it inspects what ``ib.reqAllOpenOrders()``
returns and never constructs an order. The read-only-grep test asserts no
``Order`` subclass names appear.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from factorlab.sources.ibkr.capture import capture_open_orders, decode_capture
from factorlab.sources.ibkr.normalize import DEFAULT_COUNTRY_CODE, normalize_open_orders
from factorlab.sources.ibkr.shapes import OpenOrderSnapshot

if TYPE_CHECKING:
    from ib_async import IB


def snapshot_open_orders(
    ib: IB,
    *,
    snapshot_time: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
) -> list[OpenOrderSnapshot]:
    """Return one ``OpenOrderSnapshot`` per unfilled/working order."""
    capture = capture_open_orders(ib, fetched_at=snapshot_time)
    return normalize_open_orders(decode_capture(capture.body), country_code=country_code)
