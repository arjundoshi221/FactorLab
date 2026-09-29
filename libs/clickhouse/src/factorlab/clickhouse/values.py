"""Value conventions every ClickHouse v2 writer and reader shares."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

_VERSION_LOCK = threading.Lock()
_LAST_VERSION = 0


def version(timestamp: datetime) -> int:
    """Monotonic ReplacingMergeTree version for a write made now.

    Migration and resolver versions use nanoseconds, so a live replacement must be
    greater even when a caller supplies an older source timestamp.
    """
    global _LAST_VERSION  # noqa: PLW0603 - one process-wide counter, guarded by _VERSION_LOCK
    with _VERSION_LOCK:
        _LAST_VERSION = max(
            _LAST_VERSION + 1, time.time_ns(), int(timestamp.timestamp() * 1_000_000_000)
        )
        return _LAST_VERSION


def decoded_text(value: Any) -> str:
    """Normalize ClickHouse FixedString values returned as padded bytes."""
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    return str(value)


__all__ = ["decoded_text", "version"]
