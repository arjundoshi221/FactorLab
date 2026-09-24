"""IBKR broker-mirror provider built on the shared ingestion contract.

For each configured Gateway mode the provider connects read-only, then for
every dataset (positions, account state, executions, open orders):

    capture -> ctx.archive (raw.archive, transport='tcp_socket')
            -> normalize(decoded archived bytes) -> storage.write_*(provenance)

Each ``<mode>:<dataset>`` pair is one run unit. A Gateway that is down, or a
dataset that fails, is recorded with ``ctx.fail_unit`` so the other mode and
datasets still land and the run finishes ``partial``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from factorlab.shared.ingest.provider import (
    NullProviderStorage,
    Provenance,
    ProviderStorage,
    RawCapture,
    RunContext,
)
from factorlab.sources.ibkr.capture import (
    CaptureKind,
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
from factorlab.sources.ibkr.errors import IBKRConnectError
from factorlab.sources.ibkr.normalize import DEFAULT_COUNTRY_CODE, NORMALIZERS
from factorlab.sources.ibkr.shapes import Mode, source_channel_for

if TYPE_CHECKING:
    from ib_async import IB

log = logging.getLogger(__name__)

SNAPSHOT_CLIENT_ID = 2  # docs/data-sources/us/ibkr.md clientId plan: 2 = snapshot job


class BrokerStorage(ProviderStorage, Protocol):
    """Storage surface the IBKR provider writes through."""

    def write_positions(self, rows: Sequence[Any], *, provenance: Provenance) -> int: ...
    def write_account_state(self, rows: Sequence[Any], *, provenance: Provenance) -> int: ...
    def write_executions(self, rows: Sequence[Any], *, provenance: Provenance) -> int: ...
    def write_open_orders(self, rows: Sequence[Any], *, provenance: Provenance) -> int: ...


@dataclass(frozen=True, slots=True)
class SnapshotConfig:
    modes: tuple[Mode, ...] = ("paper", "live")
    client_id: int = SNAPSHOT_CLIENT_ID
    executions_lookback: timedelta = timedelta(days=1)
    connect_attempts: int = DEFAULT_CONNECT_ATTEMPTS
    connect_timeout: int = DEFAULT_TIMEOUT_SEC
    country_code: str = DEFAULT_COUNTRY_CODE

    def __post_init__(self) -> None:
        if not self.modes:
            raise ValueError("at least one IBKR mode is required")
        if len(set(self.modes)) != len(self.modes):
            raise ValueError(f"duplicate IBKR modes: {self.modes}")


Connector = Callable[[Mode, SnapshotConfig], "IB"]


def _default_connector(mode: Mode, config: SnapshotConfig) -> IB:
    return connect_with_retry(mode, client_id=config.client_id, timeout=config.connect_timeout,
                              attempts=config.connect_attempts)


@dataclass(frozen=True, slots=True)
class _Dataset:
    name: str
    kind: CaptureKind
    capture: Callable[[IB, datetime], RawCapture]


class IBKRBrokerProvider:
    """Snapshot IBKR broker state for every configured Gateway into ``broker.*``."""

    source: ClassVar[str] = "ibkr"
    pipeline: ClassVar[str] = "ibkr_broker_snapshot"
    market_code: ClassVar[str] = "USA"

    def __init__(self, storage: BrokerStorage, config: SnapshotConfig | None = None, *,
                 connector: Connector = _default_connector,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.storage = storage
        self.config = config or SnapshotConfig()
        self._connector = connector
        self._clock = clock

    def collect(self, ctx: RunContext) -> None:
        for mode in self.config.modes:
            try:
                ib = self._connector(mode, self.config)
            except Exception as exc:  # one Gateway down must not stop the other
                # An unreachable Gateway is routine (machine off, logged out); only
                # unexpected failures need a traceback.
                if not isinstance(exc, IBKRConnectError):
                    log.warning("IBKR %s connect failed unexpectedly", mode, exc_info=True)
                ctx.fail_unit(f"{mode}:connect", exc)
                continue
            try:
                self._snapshot_mode(ctx, ib, mode)
            finally:
                disconnect(ib)

    def _datasets(self) -> tuple[_Dataset, ...]:
        lookback = self.config.executions_lookback
        return (
            _Dataset("positions", "portfolio",
                     lambda ib, at: capture_portfolio(ib, fetched_at=at)),
            _Dataset("account_state", "account_values",
                     lambda ib, at: capture_account_values(ib, fetched_at=at)),
            _Dataset("executions", "executions",
                     lambda ib, at: capture_executions(ib, since=at - lookback, fetched_at=at)),
            _Dataset("open_orders", "open_orders",
                     lambda ib, at: capture_open_orders(ib, fetched_at=at)),
        )

    def _writer(self, dataset: str) -> Callable[..., int]:
        return {
            "positions": self.storage.write_positions,
            "account_state": self.storage.write_account_state,
            "executions": self.storage.write_executions,
            "open_orders": self.storage.write_open_orders,
        }[dataset]

    def _snapshot_mode(self, ctx: RunContext, ib: IB, mode: Mode) -> None:
        channel = source_channel_for(mode)
        for dataset in self._datasets():
            unit = f"{mode}:{dataset.name}"
            try:
                capture = dataset.capture(ib, self._clock())
                raw_id = ctx.archive(capture, source_channel=channel)
                payload = decode_capture(capture.body, expected_kind=dataset.kind)
                rows = NORMALIZERS[dataset.kind](payload, country_code=self.config.country_code)
                provenance = ctx.provenance(source_channel=channel, raw_id=raw_id,
                                            as_of_time=capture.fetched_at)
                written = self._writer(dataset.name)(rows, provenance=provenance)
            except Exception as exc:  # isolate dataset failures into run units
                log.warning("IBKR %s failed", unit, exc_info=True)
                ctx.fail_unit(unit, exc)
                continue
            log.info("IBKR %s: %d rows (raw_id=%s)", unit, written, raw_id)
            ctx.succeed_unit(unit, written)


class DryRunBrokerStorage(NullProviderStorage):
    """Dry-run sink: archives and writes nothing, only counts rows per dataset."""

    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[str, int] = {}

    def _count(self, dataset: str, rows: Sequence[Any], provenance: Provenance) -> int:
        key = f"{provenance.source_channel}:{dataset}"
        self.counts[key] = self.counts.get(key, 0) + len(rows)
        return len(rows)

    def write_positions(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        return self._count("positions", rows, provenance)

    def write_account_state(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        return self._count("account_state", rows, provenance)

    def write_executions(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        return self._count("executions", rows, provenance)

    def write_open_orders(self, rows: Sequence[Any], *, provenance: Provenance) -> int:
        return self._count("open_orders", rows, provenance)
