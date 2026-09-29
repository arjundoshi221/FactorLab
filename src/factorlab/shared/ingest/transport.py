"""Shared HTTP status -> provider error taxonomy mapping for REST adapters."""

from __future__ import annotations

import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime

from factorlab.shared.ingest.errors import (
    AuthRequired,
    PermanentError,
    QuotaExhausted,
    RateLimited,
    TransientError,
)

MAX_RETRY_AFTER_SEC = 60.0


def retry_after_seconds(headers: Mapping[str, str], default: float = 1.0) -> float:
    value = headers.get("Retry-After")
    if not value:
        return default
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER_SEC)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
            return min(max(delay, 0.0), MAX_RETRY_AFTER_SEC)
        except (TypeError, ValueError, OverflowError):
            return default


def raise_for_status(provider: str, status: int, headers: Mapping[str, str], what: str, *,
                     quota_statuses: frozenset[int] = frozenset()) -> None:
    """Raise the taxonomy error for a non-200 response; return quietly on 200."""
    if status == 200:
        return
    if status in quota_statuses:
        raise QuotaExhausted(f"{provider} quota exhausted (HTTP {status})")
    if status in (401, 403):
        raise AuthRequired(f"{provider} returned HTTP {status}; credentials need renewal")
    if status == 429:
        raise RateLimited(f"{provider} rate limit", retry_after=retry_after_seconds(headers))
    if status >= 500:
        raise TransientError(f"{provider} returned HTTP {status}")
    raise PermanentError(f"{provider} returned HTTP {status} for {what}")


__all__ = ["MAX_RETRY_AFTER_SEC", "raise_for_status", "retry_after_seconds"]
