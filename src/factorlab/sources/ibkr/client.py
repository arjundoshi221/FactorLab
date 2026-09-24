"""IBKR Gateway connection factory (read-only, dual account).

Two Gateways run side-by-side, one per mode. Each mode resolves its own
endpoint so the Gateways can live on different hosts (separate containers in
production):

* host: ``IBKR_HOST_PAPER`` / ``IBKR_HOST_LIVE``, falling back to ``IBKR_HOST``
  (default ``127.0.0.1``);
* port: ``IBKR_PORT_PAPER`` (default 4002) / ``IBKR_PORT_LIVE`` (default 4001);
* client id: ``IBKR_CLIENT_ID`` unless the caller passes one.

The connection is always ``readonly=True`` — passing ``readonly=False`` raises
``IBKRReadOnlyViolation`` (the read-only rule is not opt-out).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from factorlab.core.secrets import get_secret
from factorlab.sources.ibkr.errors import IBKRConnectError, IBKRError, IBKRReadOnlyViolation
from factorlab.sources.ibkr.pacing import backoff_for
from factorlab.sources.ibkr.shapes import Mode, SourceChannel, source_channel_for

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ib_async import IB

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT_PAPER = 4002
DEFAULT_PORT_LIVE = 4001
DEFAULT_CLIENT_ID = 1
DEFAULT_TIMEOUT_SEC = 15
DEFAULT_CONNECT_ATTEMPTS = 3
CONNECT_BACKOFF_BASE_SEC = 5.0
CONNECT_BACKOFF_MAX_SEC = 60.0


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    mode: Mode
    host: str
    port: int
    client_id: int

    @property
    def source_channel(self) -> SourceChannel:
        return source_channel_for(self.mode)


def _setting(name: str, default: str) -> str:
    return (get_secret(name, default) or default).strip()


def _resolve_mode(mode: Mode | None) -> Mode:
    if mode is None:
        raw = _setting("IBKR_DEFAULT_MODE", "paper").lower()
    else:
        raw = mode.strip().lower() if isinstance(mode, str) else mode
    if raw not in ("paper", "live"):
        raise IBKRError(f"IBKR mode must be 'paper' or 'live', got {raw!r}")
    return raw  # type: ignore[return-value]


def gateway_config(mode: Mode | None = None, *, client_id: int | None = None) -> GatewayConfig:
    """Resolve the Gateway endpoint for ``mode`` from secrets/environment."""
    resolved = _resolve_mode(mode)
    suffix = resolved.upper()
    host = _setting(f"IBKR_HOST_{suffix}", _setting("IBKR_HOST", DEFAULT_HOST))
    port_key = f"IBKR_PORT_{suffix}"
    default_port = DEFAULT_PORT_PAPER if resolved == "paper" else DEFAULT_PORT_LIVE
    port_raw = _setting(port_key, str(default_port))
    client_raw = str(client_id) if client_id is not None else _setting(
        "IBKR_CLIENT_ID", str(DEFAULT_CLIENT_ID))
    try:
        return GatewayConfig(mode=resolved, host=host, port=int(port_raw), client_id=int(client_raw))
    except ValueError as exc:
        raise IBKRError(
            f"IBKR env parse error: {port_key}={port_raw!r} IBKR_CLIENT_ID={client_raw!r} ({exc})"
        ) from exc


def _read_config(mode: Mode) -> dict[str, int | str]:
    """Backward-compatible dict view of :func:`gateway_config`."""
    cfg = gateway_config(mode)
    return {"host": cfg.host, "port": cfg.port, "client_id": cfg.client_id}


def connect(
    mode: Mode | None = None,
    *,
    readonly: bool = True,
    client_id: int | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> IB:
    """Open a read-only connection to the requested IB Gateway.

    ``ib_async`` is imported lazily so importing the module does not require
    the dependency (tests can mock without installing it).
    """
    if not readonly:
        raise IBKRReadOnlyViolation(
            "FactorLab's IBKR adapter is read-only; readonly=False is not allowed."
        )

    from ib_async import IB  # local import: keeps module import cheap for tests

    cfg = gateway_config(mode, client_id=client_id)
    ib = IB()
    log.info(
        "Connecting IBKR [%s] %s:%s clientId=%s readonly=True",
        cfg.mode, cfg.host, cfg.port, cfg.client_id,
    )
    ib.connect(cfg.host, cfg.port, clientId=cfg.client_id, readonly=True, timeout=timeout)
    # Tag the mode on the ib object so downstream helpers can label rows
    # without re-reading env.
    ib._factorlab_mode = cfg.mode  # type: ignore[attr-defined]
    log.info("Connected IBKR [%s] serverVersion=%s", cfg.mode, ib.client.serverVersion())
    return ib


def connect_with_retry(
    mode: Mode,
    *,
    client_id: int | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    attempts: int = DEFAULT_CONNECT_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> IB:
    """Connect with bounded exponential backoff; raise ``IBKRConnectError`` when exhausted.

    Only transport failures are retried — configuration errors and the
    read-only guard surface immediately.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return connect(mode, readonly=True, client_id=client_id, timeout=timeout)
        except (ConnectionError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == attempts:
                break
            delay = backoff_for(attempt, base=CONNECT_BACKOFF_BASE_SEC, cap=CONNECT_BACKOFF_MAX_SEC)
            log.warning("IBKR [%s] connect attempt %d/%d failed (%s); retrying in %.0fs",
                        mode, attempt, attempts, type(exc).__name__, delay)
            sleep(delay)
    raise IBKRConnectError(
        f"IBKR [{mode}] Gateway unreachable after {attempts} attempts: "
        f"{type(last).__name__}: {last}"
    ) from last


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


def source_channel_of(ib: IB) -> SourceChannel:
    """Return the ``source_channel`` string that belongs on rows from ``ib``."""
    return source_channel_for(mode_of(ib))
