"""IBKR ``broker.snapshot`` dataset source (docs/architecture/07 §6, P6).

The same capture -> archive -> normalize path as :class:`IBKRBrokerProvider`
(which still serves the production snapshot job), expressed as a dataset
source so the engine drives it:

* ``plan`` makes one unit per ``<mode>:<dataset>`` (paper/live x positions,
  account state, executions, open orders), in Gateway order;
* ``fetch`` holds one read-only connection per mode for the run; a Gateway
  that cannot be reached fails every unit of that mode once, without
  reconnecting per unit;
* ``close`` (called by the engine after the run) disconnects.

Source channels are ``<instance>:<mode>_gateway`` (the legacy provider writes
``<mode>_gateway``); see 07 §15.1 before switching the production job.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from factorlab.shared.ingest.bindings import ProviderSettings
from factorlab.shared.ingest.datasets import Capabilities, FetchUnit, SnapshotRequest
from factorlab.shared.ingest.errors import PermanentError
from factorlab.shared.ingest.provider import RawCapture
from factorlab.sources.ibkr.capture import (
    capture_account_values,
    capture_executions,
    capture_open_orders,
    capture_portfolio,
    decode_capture,
)
from factorlab.sources.ibkr.client import (
    DEFAULT_CONNECT_ATTEMPTS,
    DEFAULT_TIMEOUT_SEC,
    connect_with_retry,
    disconnect,
)
from factorlab.sources.ibkr.normalize import DEFAULT_COUNTRY_CODE, NORMALIZERS
from factorlab.sources.ibkr.provider import SNAPSHOT_CLIENT_ID

if TYPE_CHECKING:
    from ib_async import IB

log = logging.getLogger(__name__)

# (dataset, capture kind) in the order the legacy provider snapshots them.
DATASETS: tuple[tuple[str, str], ...] = (
    ("positions", "portfolio"),
    ("account_state", "account_values"),
    ("executions", "executions"),
    ("open_orders", "open_orders"),
)


class IbkrSettings(ProviderSettings):
    modes: tuple[str, ...] = ("paper", "live")
    client_id: int = SNAPSHOT_CLIENT_ID
    executions_lookback_hours: int = Field(default=24, ge=1)
    connect_attempts: int = Field(default=DEFAULT_CONNECT_ATTEMPTS, ge=1)
    connect_timeout: int = Field(default=DEFAULT_TIMEOUT_SEC, ge=1)
    country_code: str = DEFAULT_COUNTRY_CODE


Connector = Callable[[str, IbkrSettings], "IB"]


def _default_connector(mode: str, settings: IbkrSettings) -> IB:
    return connect_with_retry(mode, client_id=settings.client_id,  # type: ignore[arg-type]
                              timeout=settings.connect_timeout,
                              attempts=settings.connect_attempts)


class IbkrBrokerSnapshot:
    provider: ClassVar[str] = "ibkr"
    dataset: ClassVar[str] = "broker.snapshot"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=frozenset({"USA"}))
    settings_model: ClassVar[type[IbkrSettings]] = IbkrSettings

    def __init__(self, settings: IbkrSettings, *, instance: str = "ibkr",
                 connector: Connector = _default_connector,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.settings = settings
        self.instance = instance
        self._connector = connector
        self._clock = clock
        self._connections: dict[str, IB] = {}
        self._failed: dict[str, str] = {}

    def plan(self, request: SnapshotRequest) -> Sequence[FetchUnit]:
        modes = tuple(request.params.get("modes") or self.settings.modes)
        unknown = sorted(set(modes) - {"paper", "live"})
        if unknown:
            raise ValueError(f"unknown IBKR modes {unknown}")
        return [FetchUnit(f"{mode}:{name}", f"{self.instance}:{mode}_gateway",
                          params={"mode": mode, "kind": kind})
                for mode in dict.fromkeys(modes) for name, kind in DATASETS]

    def _connection(self, mode: str) -> IB:
        if mode in self._failed:
            raise PermanentError(f"IBKR {mode} Gateway unavailable: {self._failed[mode]}")
        if mode not in self._connections:
            try:
                self._connections[mode] = self._connector(mode, self.settings)
            except Exception as exc:  # one Gateway down must not stop the other
                self._failed[mode] = type(exc).__name__
                raise PermanentError(f"IBKR {mode} Gateway unavailable: "
                                     f"{type(exc).__name__}") from exc
        return self._connections[mode]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        ib = self._connection(str(unit.params["mode"]))
        now = self._clock()
        kind = unit.params["kind"]
        if kind == "portfolio":
            return capture_portfolio(ib, fetched_at=now)
        if kind == "account_values":
            return capture_account_values(ib, fetched_at=now)
        if kind == "executions":
            since = now - timedelta(hours=self.settings.executions_lookback_hours)
            return capture_executions(ib, since=since, fetched_at=now)
        return capture_open_orders(ib, fetched_at=now)

    def normalize(self, capture: RawCapture) -> Sequence[Any]:
        payload = decode_capture(capture.body)
        return NORMALIZERS[payload.kind](payload, country_code=self.settings.country_code)

    def close(self) -> None:
        for mode, ib in list(self._connections.items()):
            try:
                disconnect(ib)
            except Exception:
                log.warning("IBKR %s disconnect failed", mode, exc_info=True)
        self._connections.clear()
        self._failed.clear()


__all__ = ["DATASETS", "IbkrBrokerSnapshot", "IbkrSettings"]
