"""IBKR Gateway connection factory (read-only, dual account).

Two Gateways run side-by-side — paper on ``IBKR_PORT_PAPER`` (default 4002)
and live on ``IBKR_PORT_LIVE`` (default 4001). Callers pick a mode; the
connection is always ``readonly=True`` — passing ``readonly=False`` raises
``IBKRReadOnlyViolation`` (the read-only rule is not opt-out).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from dotenv import find_dotenv, load_dotenv

from factorlab.core.secrets import get_secret
from factorlab.sources.ibkr.errors import IBKRError, IBKRReadOnlyViolation

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ib_async import IB

log = logging.getLogger(__name__)

Mode = Literal["paper", "live"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT_PAPER = 4002
DEFAULT_PORT_LIVE = 4001
DEFAULT_CLIENT_ID = 1
DEFAULT_TIMEOUT_SEC = 15


def _resolve_mode(mode: Mode | None) -> Mode:
    if mode is None:
        raw = (get_secret("IBKR_DEFAULT_MODE", "paper") or "paper").strip().lower()
    else:
        raw = mode.strip().lower() if isinstance(mode, str) else mode
    if raw not in ("paper", "live"):
        raise IBKRError(f"IBKR mode must be 'paper' or 'live', got {raw!r}")
    return raw  # type: ignore[return-value]


def _read_config(mode: Mode) -> dict[str, int | str]:
    load_dotenv(find_dotenv(usecwd=True))
    host = (get_secret("IBKR_HOST", DEFAULT_HOST) or DEFAULT_HOST).strip()
    port_key = f"IBKR_PORT_{mode.upper()}"
    default_port = DEFAULT_PORT_PAPER if mode == "paper" else DEFAULT_PORT_LIVE
    port_raw = (get_secret(port_key, str(default_port)) or str(default_port)).strip()
    client_raw = (get_secret("IBKR_CLIENT_ID", str(DEFAULT_CLIENT_ID)) or str(DEFAULT_CLIENT_ID)).strip()
    try:
        return {"host": host, "port": int(port_raw), "client_id": int(client_raw)}
    except ValueError as exc:
        raise IBKRError(
            f"IBKR env parse error: {port_key}={port_raw!r} IBKR_CLIENT_ID={client_raw!r} ({exc})"
        ) from exc


def connect(
    mode: Mode | None = None,
    *,
    readonly: bool = True,
    client_id: int | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> IB:
    """Open a read-only connection to the requested IB Gateway.

    Attempts to bring in ``ib_async`` lazily so importing the module does
    not require the dependency (tests can mock without installing it).
    """
    if not readonly:
        raise IBKRReadOnlyViolation(
            "FactorLab's IBKR adapter is read-only; readonly=False is not allowed."
        )

    from ib_async import IB  # local import: keeps module import cheap for tests

    resolved = _resolve_mode(mode)
    cfg = _read_config(resolved)
    cid = int(client_id) if client_id is not None else int(cfg["client_id"])

    ib = IB()
    log.info(
        "Connecting IBKR [%s] %s:%s clientId=%s readonly=True",
        resolved, cfg["host"], cfg["port"], cid,
    )
    ib.connect(
        cfg["host"],
        int(cfg["port"]),
        clientId=cid,
        readonly=True,
        timeout=timeout,
    )
    # Tag the mode on the ib object so downstream helpers can label rows
    # without re-reading env.
    ib._factorlab_mode = resolved  # type: ignore[attr-defined]
    log.info(
        "Connected IBKR [%s] serverVersion=%s",
        resolved, ib.client.serverVersion(),
    )
    return ib


def disconnect(ib: IB) -> None:
    """Close the Gateway connection if still open. Idempotent."""
    if ib.isConnected():
        mode = getattr(ib, "_factorlab_mode", "?")
        ib.disconnect()
        log.info("Disconnected IBKR [%s]", mode)


@contextmanager
def connected(
    mode: Mode | None = None,
    *,
    client_id: int | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> Iterator[IB]:
    """Context-manager wrapper: connect + guaranteed disconnect."""
    ib = connect(mode, readonly=True, client_id=client_id, timeout=timeout)
    try:
        yield ib
    finally:
        disconnect(ib)


def mode_of(ib: IB) -> Mode:
    """Return the mode tag we attached to ``ib`` at connect time."""
    m = getattr(ib, "_factorlab_mode", None)
    if m not in ("paper", "live"):
        raise IBKRError(f"IB object was not connected via factorlab.sources.ibkr.client: mode={m!r}")
    return m  # type: ignore[return-value]


def source_channel_of(ib: IB) -> Literal["paper_gateway", "live_gateway"]:
    """Return the ``source_channel`` string that belongs on rows from ``ib``."""
    return f"{mode_of(ib)}_gateway"  # type: ignore[return-value]
