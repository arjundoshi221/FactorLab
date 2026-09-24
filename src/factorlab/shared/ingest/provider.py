"""Typed ingestion contract shared by FactorLab data providers.

Every provider writes canonical v2 data the same way::

    fetch        -> RawCapture                      (provider adapter, impure)
    archive      -> raw_id = storage.archive_raw()  (raw.archive, immutable)
    normalize    -> rows = normalize(capture.body)  (pure; replayable from raw)
    write        -> storage.write_*(rows, provenance=ctx.provenance(...))

all inside one ``meta.ingestion_runs`` row opened by :func:`ingestion_run`.
Normalizing from the archived bytes (never from live client objects) is what
lets any stored snapshot be replayed exactly from ``raw.archive``.

A provider is anything with a ``source`` name and a ``collect(ctx)`` method;
:func:`run_provider` wraps it in a run and reports a :class:`RunSummary`.
Per-unit failures (one account, one symbol, one gateway) are recorded with
``ctx.fail_unit`` so a run finishes ``partial`` rather than aborting.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable
from uuid import UUID

from factorlab.shared.ingest.security import redact_error

log = logging.getLogger(__name__)

Transport = Literal["http", "websocket", "tcp_socket", "grpc", "file", "sftp"]
RunStatus = Literal["success", "partial", "failed", "cancelled"]

MAX_RUN_ERROR_CHARS = 2000


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime, got {value!r}")


@dataclass(frozen=True, slots=True)
class RawCapture:
    """One provider response exactly as received, ready for ``raw.archive``."""

    body: bytes
    request_key: str
    transport: Transport
    fetched_at: datetime
    source_url: str = ""
    content_type: str = "application/json"
    status_code: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes):
            raise TypeError("RawCapture.body must be bytes")
        _require_utc("fetched_at", self.fetched_at)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Lineage and point-in-time columns stamped onto every fact row at write time."""

    source: str
    source_channel: str
    raw_id: UUID | None
    ingest_run_id: UUID
    as_of_time: datetime
    ingested_at: datetime

    def __post_init__(self) -> None:
        _require_utc("as_of_time", self.as_of_time)
        _require_utc("ingested_at", self.ingested_at)

    def columns(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_channel": self.source_channel,
            "raw_id": self.raw_id,
            "ingest_run_id": self.ingest_run_id,
            "as_of_time": self.as_of_time,
            "ingested_at": self.ingested_at,
        }


class RunHandle(Protocol):
    @property
    def run_id(self) -> UUID: ...


@runtime_checkable
class RawArchive(Protocol):
    def archive_raw(self, capture: RawCapture, *, source: str, source_channel: str) -> UUID: ...


@runtime_checkable
class IngestionRuns(Protocol):
    def start_ingestion_run(
        self, *, pipeline: str, source: str, market_code: str = ..., universe: str = "",
        requested_series: int = 0, metadata: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def finish_ingestion_run(
        self, handle: Any, *, status: str, successful_series: int = 0,
        failed_series: int = 0, rows_written: int = 0, error: str | None = None,
    ) -> None: ...


@runtime_checkable
class ProviderStorage(RawArchive, IngestionRuns, Protocol):
    """Minimum storage surface a provider needs; write methods are provider-specific."""


@dataclass(frozen=True, slots=True)
class UnitOutcome:
    name: str
    rows_written: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: UUID
    source: str
    pipeline: str
    status: RunStatus
    rows_written: int
    units: tuple[UnitOutcome, ...]

    @property
    def failed_units(self) -> tuple[UnitOutcome, ...]:
        return tuple(unit for unit in self.units if not unit.ok)


def _safe_error(error: BaseException | str) -> str:
    text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    return redact_error(text) or type(error).__name__


class RunContext:
    """Per-run handle a provider uses to archive captures and record unit outcomes."""

    def __init__(self, storage: ProviderStorage, handle: RunHandle, *,
                 source: str, pipeline: str) -> None:
        self.storage = storage
        self.handle = handle
        self.source = source
        self.pipeline = pipeline
        self._units: list[UnitOutcome] = []

    @property
    def run_id(self) -> UUID:
        return self.handle.run_id

    def archive(self, capture: RawCapture, *, source_channel: str) -> UUID:
        return self.storage.archive_raw(capture, source=self.source, source_channel=source_channel)

    def provenance(self, *, source_channel: str, raw_id: UUID | None,
                   as_of_time: datetime, ingested_at: datetime | None = None) -> Provenance:
        return Provenance(
            source=self.source, source_channel=source_channel, raw_id=raw_id,
            ingest_run_id=self.run_id, as_of_time=as_of_time,
            ingested_at=ingested_at or datetime.now(UTC),
        )

    def succeed_unit(self, name: str, rows_written: int) -> None:
        self._units.append(UnitOutcome(name=name, rows_written=int(rows_written)))

    def fail_unit(self, name: str, error: BaseException | str, *, rows_written: int = 0) -> None:
        message = _safe_error(error)
        log.warning("[%s] unit %s failed: %s", self.source, name, message)
        self._units.append(UnitOutcome(name=name, rows_written=int(rows_written), error=message))

    @property
    def units(self) -> tuple[UnitOutcome, ...]:
        return tuple(self._units)

    @property
    def rows_written(self) -> int:
        return sum(unit.rows_written for unit in self._units)

    @property
    def status(self) -> RunStatus:
        failed = sum(1 for unit in self._units if not unit.ok)
        if not failed:
            return "success"
        return "partial" if failed < len(self._units) else "failed"

    def error_text(self) -> str | None:
        errors = [f"{unit.name}: {unit.error}" for unit in self._units if unit.error]
        return "; ".join(errors)[:MAX_RUN_ERROR_CHARS] if errors else None

    def summary(self, status: RunStatus | None = None) -> RunSummary:
        return RunSummary(
            run_id=self.run_id, source=self.source, pipeline=self.pipeline,
            status=status or self.status, rows_written=self.rows_written, units=self.units,
        )


@contextmanager
def ingestion_run(
    storage: ProviderStorage,
    *,
    pipeline: str,
    source: str,
    market_code: str,
    universe: str = "",
    requested_series: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[RunContext]:
    """Open a ``meta.ingestion_runs`` row and always write its terminal state.

    Unit outcomes decide ``success``/``partial``/``failed``. An exception escaping
    the block marks the run ``failed`` (``cancelled`` for interrupts) and re-raises.
    """
    handle = storage.start_ingestion_run(
        pipeline=pipeline, source=source, market_code=market_code, universe=universe,
        requested_series=requested_series, metadata=dict(metadata or {}),
    )
    ctx = RunContext(storage, handle, source=source, pipeline=pipeline)
    try:
        yield ctx
    except BaseException as exc:
        status: RunStatus = "cancelled" if isinstance(exc, KeyboardInterrupt | SystemExit) else "failed"
        storage.finish_ingestion_run(
            handle, status=status,
            successful_series=sum(1 for unit in ctx.units if unit.ok),
            failed_series=sum(1 for unit in ctx.units if not unit.ok),
            rows_written=ctx.rows_written,
            error=_safe_error(exc)[:MAX_RUN_ERROR_CHARS],
        )
        raise
    storage.finish_ingestion_run(
        handle, status=ctx.status,
        successful_series=sum(1 for unit in ctx.units if unit.ok),
        failed_series=sum(1 for unit in ctx.units if not unit.ok),
        rows_written=ctx.rows_written, error=ctx.error_text(),
    )


class Provider(Protocol):
    """A data provider: fetches, archives, normalizes and writes inside a run."""

    source: ClassVar[str]
    pipeline: ClassVar[str]
    market_code: ClassVar[str]

    def collect(self, ctx: RunContext) -> None: ...


def run_provider(
    provider: Provider,
    storage: ProviderStorage,
    *,
    universe: str = "",
    requested_series: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> RunSummary:
    """Run ``provider.collect`` inside one ingestion run and return its summary."""
    with ingestion_run(
        storage, pipeline=provider.pipeline, source=provider.source,
        market_code=provider.market_code, universe=universe,
        requested_series=requested_series, metadata=metadata,
    ) as ctx:
        provider.collect(ctx)
    return ctx.summary()


@dataclass(frozen=True, slots=True)
class _NullRunHandle:
    run_id: UUID


class NullProviderStorage:
    """Dry-run storage: satisfies :class:`ProviderStorage` without writing anything.

    Subclass it to add a provider's ``write_*`` methods for dry runs.
    """

    def __init__(self) -> None:
        self.archived: list[tuple[str, str, RawCapture]] = []
        self.finished: list[dict[str, Any]] = []

    def archive_raw(self, capture: RawCapture, *, source: str, source_channel: str) -> UUID:
        self.archived.append((source, source_channel, capture))
        return uuid.uuid4()

    def start_ingestion_run(self, **_: Any) -> _NullRunHandle:
        return _NullRunHandle(run_id=uuid.uuid4())

    def finish_ingestion_run(self, handle: Any, **kwargs: Any) -> None:
        self.finished.append({"run_id": handle.run_id, **kwargs})


__all__ = [
    "IngestionRuns",
    "NullProviderStorage",
    "Provenance",
    "Provider",
    "ProviderStorage",
    "RawArchive",
    "RawCapture",
    "RunContext",
    "RunStatus",
    "RunSummary",
    "Transport",
    "UnitOutcome",
    "ingestion_run",
    "run_provider",
]
