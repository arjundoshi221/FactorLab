"""IBKR adapter exceptions."""

from __future__ import annotations


class IBKRError(RuntimeError):
    """Base error for anything wrong with the IBKR adapter."""


class IBKRConnectError(IBKRError):
    """Raised when a Gateway stays unreachable after the bounded connect retries."""


class IBKRPacingError(IBKRError):
    """Raised when the client-side rate limiter would exceed IBKR's caps
    or when the server returns error code 162 (pacing violation)."""


class IBKRCaptureError(IBKRError):
    """Raised when an archived IBKR payload cannot be decoded for normalization."""


class IBKRReadOnlyViolation(IBKRError):
    """Raised if a caller attempts to open a non-read-only connection.

    FactorLab's IBKR adapter is read-only by contract; any code path that
    tries to disable ``readonly`` is a bug and must fail loudly.
    """
