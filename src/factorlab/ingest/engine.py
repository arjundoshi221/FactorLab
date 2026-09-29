"""Provider-agnostic ingestion engine (docs/architecture/07 §11).

Drives any registered :class:`~factorlab.ingest.datasets.DatasetSource`
into the sink for its dataset, inside the existing ``ingestion_run`` contract::

    plan(request) -> for each unit: fetch -> archive -> normalize -> sink.<write>

Each unit is one ``RunContext`` outcome, so one bad symbol or window leaves the
run ``partial`` instead of aborting it (the ``IBKRBrokerProvider`` pattern).
The engine never imports a concrete provider: sources come from the registry.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from factorlab.ingest.bindings import Binding
from factorlab.ingest.datasets import (
    BarRequest,
    DatasetSource,
    FetchUnit,
    ReferenceRequest,
    SeriesWindow,
    WriteResult,
    dataset,
)
from factorlab.ingest.datasets.ports import ArchivedCapture, CheckpointStore, ReferenceReader
from factorlab.ingest.errors import AuthRequired, classify, retry_after
from factorlab.ingest.provider import (
    ProviderStorage,
    RawCapture,
    RunContext,
    RunSummary,
    run_provider,
)

log = logging.getLogger(__name__)

DEFAULT_RETRIES = 2
MAX_SLEEP_SEC = 60.0


def _rows_written(result: WriteResult | int) -> tuple[int, int]:
    if isinstance(result, WriteResult):
        return result.rows_written, result.unresolved
    return int(result), 0


class DatasetProvider:
    """Adapts ``(binding, source, sink, units)`` to the ``Provider`` protocol."""

    def __init__(self, binding: Binding, adapter: DatasetSource[Any, Any], sink: ProviderStorage,
                 units: Sequence[FetchUnit], *, unmapped: Sequence[str] = (),
                 retries: int = DEFAULT_RETRIES, max_sleep: float = MAX_SLEEP_SEC,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if adapter.provider != binding.provider or adapter.dataset != binding.dataset:
            raise ValueError(
                f"source {adapter.provider}/{adapter.dataset} does not match binding "
                f"{binding.provider}/{binding.dataset}"
            )
        self.source = binding.source_name
        self.pipeline = binding.pipeline
        self.market_code = binding.market
        self.binding = binding
        self.adapter = adapter
        self.sink = sink
        self.units = tuple(units)
        self.unmapped = tuple(unmapped)
        self.retries = retries
        self.max_sleep = max_sleep
        self._sleep = sleep
        self.unresolved = 0
        self.resolved = 0
        spec = dataset(binding.dataset)
        self._write = getattr(sink, spec.sink_method)
        self._write_kwargs: dict[str, Any] = (
            {"mode": binding.reference_mode} if spec.reference else {}
        )
        # A shadow of a table without `source` in its key would overwrite the incumbent's
        # rows, so it fetches, archives and normalizes for real but only counts (07 §9.2).
        self.counts_only = (binding.role == "shadow" and not spec.reference
                            and not spec.source_keyed)
        self.shadow_rows = 0

    def _channel_ok(self, channel: str) -> bool:
        instance = self.binding.instance_name
        return channel == instance or channel.startswith(f"{instance}:")

    def _fetch(self, unit: FetchUnit) -> RawCapture:
        attempt = 0
        rate_retried = False
        while True:
            try:
                return self.adapter.fetch(unit)
            except Exception as exc:
                kind = classify(exc)
                if kind == "rate_limited" and not rate_retried:
                    rate_retried = True
                    self._sleep(min(retry_after(exc), self.max_sleep))
                    continue
                if kind == "transient" and attempt < self.retries:
                    attempt += 1
                    self._sleep(min(float(2 ** (attempt - 1)), self.max_sleep))
                    continue
                raise

    def collect(self, ctx: RunContext) -> None:
        if self.unmapped:
            alias_kind = self.adapter.capabilities.alias_kind or "alias"
            ctx.fail_unit(f"unmapped:{alias_kind}",
                          f"{len(self.unmapped)} listing(s) have no {alias_kind} alias")
        auth_error: BaseException | None = None
        for unit in self.units:
            if auth_error is not None:
                ctx.fail_unit(unit.name, AuthRequired(f"skipped after auth failure: {auth_error}"))
                continue
            if not self._channel_ok(unit.source_channel):
                ctx.fail_unit(unit.name, ValueError(
                    f"source_channel {unit.source_channel!r} must start with "
                    f"{self.binding.instance_name!r}"))
                continue
            try:
                capture = self._fetch(unit)
            except Exception as exc:  # noqa: BLE001 - isolate failures into run units
                if classify(exc) == "auth":
                    auth_error = exc
                ctx.fail_unit(unit.name, exc)
                continue
            try:
                raw_id = ctx.archive(capture, source_channel=unit.source_channel)
                self._write_unit(ctx, unit.name, unit.source_channel, raw_id, capture)
            except Exception as exc:
                log.warning("[%s] unit %s failed", self.pipeline, unit.name, exc_info=True)
                ctx.fail_unit(unit.name, exc)

    def _write_unit(self, ctx: RunContext, name: str, channel: str, raw_id: UUID | None,
                    capture: RawCapture) -> None:
        rows = self.adapter.normalize(capture)
        if self.counts_only:
            self.shadow_rows += len(rows)
            log.info("[%s] %s: shadow normalized %d row(s); not written", self.pipeline, name,
                     len(rows))
            ctx.succeed_unit(name, 0)
            return
        provenance = ctx.provenance(source_channel=channel, raw_id=raw_id,
                                    as_of_time=capture.fetched_at)
        result = self._write(rows, provenance=provenance, **self._write_kwargs)
        written, unresolved = _rows_written(result)
        self.unresolved += unresolved
        self.resolved += result.resolved if isinstance(result, WriteResult) else written
        if unresolved:
            log.info("[%s] %s: %d row(s) parked as unresolved", self.pipeline, name, unresolved)
        ctx.succeed_unit(name, written)


class ReplayProvider(DatasetProvider):
    """Re-normalizes archived captures and writes them without re-fetching (07 §11.4)."""

    def __init__(self, binding: Binding, adapter: DatasetSource[Any, Any], sink: ProviderStorage,
                 captures: Iterable[ArchivedCapture]) -> None:
        super().__init__(binding, adapter, sink, units=())
        self.captures = tuple(captures)

    def collect(self, ctx: RunContext) -> None:
        for item in self.captures:
            name = f"replay:{item.raw_id}"
            if item.source not in (self.binding.provider, self.binding.source_name):
                ctx.fail_unit(name, ValueError(
                    f"capture {item.raw_id} belongs to {item.source!r}, not "
                    f"{self.binding.provider!r}"))
                continue
            try:
                self._write_unit(ctx, name, item.source_channel, item.raw_id, item.capture)
            except Exception as exc:  # noqa: BLE001 - one bad capture must not stop the replay
                ctx.fail_unit(name, exc)


def _run_metadata(binding: Binding, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "dataset": binding.dataset, "instance": binding.instance_name,
        "role": binding.role, "resolution": binding.resolution or "", **dict(extra or {}),
    }


def run_binding(binding: Binding, adapter: DatasetSource[Any, Any], sink: ProviderStorage,
                request: Any, *, unmapped: Sequence[str] = (),
                metadata: Mapping[str, Any] | None = None, **kwargs: Any) -> RunSummary:
    """Plan ``request`` with ``adapter`` and run every unit into ``sink`` in one run."""
    if not binding.writes:
        raise ValueError(f"binding {binding.pipeline} is disabled")
    units = tuple(adapter.plan(request))
    provider = DatasetProvider(binding, adapter, sink, units, unmapped=unmapped, **kwargs)
    try:
        return run_provider(
            provider, sink, universe=str(binding.params.get("universe", "")),
            requested_series=len(units), metadata=_run_metadata(binding, metadata),
        )
    finally:
        close = getattr(adapter, "close", None)  # sources holding connections (e.g. IBKR)
        if callable(close):
            close()


def replay(binding: Binding, adapter: DatasetSource[Any, Any], sink: ProviderStorage,
           captures: Iterable[ArchivedCapture]) -> RunSummary:
    captures = tuple(captures)
    provider = ReplayProvider(binding, adapter, sink, captures)
    return run_provider(
        provider, sink, requested_series=len(captures),
        metadata=_run_metadata(binding, {"replay_of": [str(c.raw_id) for c in captures]}),
    )


MARKET_COUNTRY = {"IND": "IN", "USA": "US"}


def source_priority_rows(bindings: Iterable[Binding]) -> list[dict[str, Any]]:
    """Bindings of source-keyed datasets -> ``ref.source_priorities`` rows (07 §10).

    Disabled bindings are synced too (inactive) so switching a provider off is recorded,
    and a shadow is stored under its own ``<provider>:shadow`` source, never selected.
    """
    rows = []
    for binding in bindings:
        if not dataset(binding.dataset).source_keyed:
            continue
        rows.append({
            "dataset": binding.dataset,
            "country_code": MARKET_COUNTRY.get(binding.market, binding.market),
            "resolution": binding.resolution or "", "source": binding.source_name,
            "priority": binding.priority, "role": binding.role,
        })
    return rows


def reference_request(binding: Binding, listing_ids: Sequence[UUID] = (), *,
                      reference: ReferenceReader | None = None) -> ReferenceRequest:
    """Master-file providers need only the market; per-symbol providers get canonical refs."""
    instruments: tuple = ()
    if listing_ids:
        if reference is None:
            raise ValueError("listing_ids need a ReferenceReader to describe them")
        refs = reference.natural_refs(listing_ids)
        instruments = tuple(refs[listing] for listing in listing_ids if listing in refs)
    return ReferenceRequest(market=binding.market, params=dict(binding.params),
                            instruments=instruments)


def bar_request(binding: Binding, adapter: DatasetSource[Any, Any], listing_ids: Sequence[UUID],
                *, reference: ReferenceReader, checkpoints: CheckpointStore, now: datetime,
                default_lookback: timedelta = timedelta(days=1),
                ) -> tuple[BarRequest, tuple[str, ...]]:
    """Canonical universe -> provider-aliased windows starting at each listing's watermark.

    Returns the request and the listing ids that have no alias of the source's
    ``alias_kind`` (recorded by :func:`run_binding` as an ``unmapped`` unit).
    """
    if binding.resolution is None:
        raise ValueError(f"{binding.pipeline} has no resolution")
    caps = adapter.capabilities
    refs = reference.aliases_for(listing_ids, alias_kind=caps.alias_kind)
    unmapped = tuple(str(listing) for listing in listing_ids if listing not in refs)
    mapped = [listing for listing in listing_ids if listing in refs]
    marks = checkpoints.watermarks(mapped, dataset=binding.dataset, source=binding.source_name,
                                   resolution=binding.resolution) if mapped else {}
    floor = now - caps.max_lookback if caps.max_lookback else None
    windows = []
    for listing in mapped:
        start = marks.get(listing) or now - default_lookback
        if floor is not None and start < floor:
            start = floor
        if start < now:
            windows.append(SeriesWindow(refs[listing], start, now))
    return BarRequest(market=binding.market, resolution=binding.resolution,
                      series=tuple(windows), params=dict(binding.params)), unmapped


__all__ = [
    "DatasetProvider",
    "ReplayProvider",
    "bar_request",
    "reference_request",
    "replay",
    "run_binding",
]
