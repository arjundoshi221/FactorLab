"""Executions pull (capture + normalize, no archive).

``exec_id`` is IBKR's immutable key; deduplication is the storage layer's job
(pull windows always overlap). ``reqExecutions`` defaults to today only; pass
``since`` to widen via ``ExecutionFilter(time=...)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from factorlab.sources.ibkr.capture import capture_executions, decode_capture
from factorlab.sources.ibkr.normalize import (
    DEFAULT_COUNTRY_CODE,
    normalize_executions,
)
from factorlab.sources.ibkr.normalize import (
    normalize_exec_time as _normalize_exec_time,
)
from factorlab.sources.ibkr.normalize import (
    normalize_side as _normalize_side,
)
from factorlab.sources.ibkr.shapes import ExecutionRecord

if TYPE_CHECKING:
    from ib_async import IB

__all__ = ["_normalize_exec_time", "_normalize_side", "pull_executions"]


def pull_executions(
    ib: IB,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
    country_code: str = DEFAULT_COUNTRY_CODE,
) -> list[ExecutionRecord]:
    """Return one ``ExecutionRecord`` per fill.

    ``since``: lower bound (UTC-aware). ``None`` = IBKR's default (today).
    ``now``: injected capture clock (tests).
    """
    capture = capture_executions(ib, since=since, fetched_at=now)
    return normalize_executions(decode_capture(capture.body), country_code=country_code)
