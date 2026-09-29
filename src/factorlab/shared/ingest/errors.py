"""Provider error taxonomy used by the ingestion engine (docs/architecture/07 §6.3).

Source adapters raise (or subclass) these so the engine can decide, without
knowing the vendor, whether a unit is retried, failed, or ends the instance's
run early:

    AuthRequired       -> fail this unit and every remaining unit of the instance
    RateLimited        -> sleep ``retry_after`` (bounded), retry once, then fail
    TransientError     -> retry with backoff, then fail
    PermanentError     -> fail, no retry
    NormalizationError -> fail; the capture is already archived for replay

Any other exception fails the unit, as ``RunContext.fail_unit`` always has.
"""

from __future__ import annotations

from typing import Literal

ErrorKind = Literal["auth", "rate_limited", "transient", "permanent", "normalization", "other"]


class ProviderError(RuntimeError):
    """Base class for classified provider failures."""


class AuthRequired(ProviderError):
    """Credentials are missing or expired; nothing more can be fetched this run."""


class QuotaExhausted(AuthRequired):
    """The account's request quota is spent (e.g. an EODHD daily limit).

    Classified like ``AuthRequired``: the instance's remaining units are skipped,
    because every further call would fail the same way until the quota resets.
    """


class RateLimited(ProviderError):
    """The vendor throttled the request; ``retry_after`` is in seconds."""

    def __init__(self, message: str, *, retry_after: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after = max(float(retry_after), 0.0)


class TransientError(ProviderError):
    """Network errors, 5xx and timeouts: worth retrying."""


class PermanentError(ProviderError):
    """4xx, unknown instrument, contract change: retrying will not help."""


class NormalizationError(ProviderError):
    """Captured bytes could not be turned into records."""


def classify(error: BaseException) -> ErrorKind:
    """Map an exception to the engine's retry policy.

    Duck-types ``retry_after_sec`` so throttle exceptions that carry it are
    honoured without subclassing :class:`RateLimited`.
    """
    if isinstance(error, AuthRequired):
        return "auth"
    if isinstance(error, RateLimited) or hasattr(error, "retry_after_sec"):
        return "rate_limited"
    if isinstance(error, TransientError):
        return "transient"
    if isinstance(error, PermanentError):
        return "permanent"
    if isinstance(error, NormalizationError):
        return "normalization"
    return "other"


def retry_after(error: BaseException, default: float = 1.0) -> float:
    value = getattr(error, "retry_after", None)
    if value is None:
        value = getattr(error, "retry_after_sec", None)
    try:
        return max(float(value), 0.0) if value is not None else default
    except (TypeError, ValueError):
        return default


__all__ = [
    "AuthRequired",
    "ErrorKind",
    "NormalizationError",
    "PermanentError",
    "ProviderError",
    "QuotaExhausted",
    "RateLimited",
    "TransientError",
    "classify",
    "retry_after",
]
