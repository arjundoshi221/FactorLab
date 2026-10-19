"""India scheduling and inventory; provider I/O and writes run through the engine."""

from __future__ import annotations

import argparse
import csv
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import yaml

from factorlab.core import paths
from factorlab.core.logging import configure_logging
from factorlab.ingest.bindings import Binding, load_bindings, load_provider_settings
from factorlab.ingest.datasets import (
    BarRequest,
    ContractRecord,
    InstrumentRecord,
    InstrumentRef,
    ReferenceRequest,
    SeriesWindow,
    WriteResult,
)
from factorlab.ingest.datasets.reference import ReferenceMode
from factorlab.ingest.engine import collect_binding
from factorlab.ingest.provider import Provenance, RunSummary, ingestion_run
from factorlab.runtime.monitor import monitor_client, monitoring_sleep
from factorlab.runtime.signals import GracefulShutdown
from factorlab.sources.upstox.client import UpstoxClient
from factorlab.sources.upstox.settings import UpstoxSettings
from factorlab.sources.upstox.sources import (
    UpstoxBars,
    UpstoxContractBars,
    UpstoxContracts,
    UpstoxListings,
)
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.sinks.identity import IdentityResolver
from factorlab.storage.v2_india import V2IndiaStorage

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
FULL_EQUITY_UNIVERSE = "full_nse_eq"
OPEN = clock_time(3, 45)
CLOSE = clock_time(10, 2)
SOURCE_CLASSES = {
    "ref.listings": UpstoxListings,
    "ref.contracts": UpstoxContracts,
    "market.bars": UpstoxBars,
    "market.futures_contract_bars": UpstoxContractBars,
}


class IndiaSink(ClickHouseSinks):
    """Retain the successfully written master for exact inventory selection."""

    def __init__(self, storage: V2IndiaStorage) -> None:
        super().__init__(storage)
        self.listings: list[InstrumentRecord] = []
        self.contracts: list[ContractRecord] = []

    def upsert_instruments(
        self,
        rows: Sequence[InstrumentRecord],
        *,
        provenance: Provenance,
        mode: ReferenceMode = "authoritative",
    ) -> WriteResult:
        results = []
        for offset in range(0, len(rows), 20):
            results.append(
                super().upsert_instruments(
                    rows[offset : offset + 20], provenance=provenance, mode=mode
                )
            )
            monitor_client().tick()
        result = WriteResult(
            sum(r.rows_written for r in results),
            sum(r.unresolved for r in results),
            sum(r.resolved for r in results),
        )
        self.listings.extend(rows)
        return result

    def upsert_contracts(
        self,
        rows: Sequence[ContractRecord],
        *,
        provenance: Provenance,
        mode: ReferenceMode = "authoritative",
    ) -> WriteResult:
        results = []
        for offset in range(0, len(rows), 20):
            results.append(
                super().upsert_contracts(
                    rows[offset : offset + 20], provenance=provenance, mode=mode
                )
            )
            monitor_client().tick()
        result = WriteResult(
            sum(r.rows_written for r in results),
            sum(r.unresolved for r in results),
            sum(r.resolved for r in results),
        )
        self.contracts.extend(rows)
        return result


@dataclass(frozen=True)
class Series:
    ref: InstrumentRef
    listing_id: UUID
    contract_id: UUID | None = None

    def storage_key(self) -> dict[str, Any]:
        return {
            "instrument_id": self.listing_id,
            "contract_id": self.contract_id,
            "symbol": self.ref.trading_symbol,
        }


def universe_symbols(name: str) -> set[str] | None:
    if name == FULL_EQUITY_UNIVERSE:
        return None
    config = yaml.safe_load(
        (paths.config_dir() / "universes/india.yaml").read_text(encoding="utf-8")
    )
    definition = config["universes"][name]
    if "symbols" in definition:
        return set(definition["symbols"])
    target = (paths.home() / definition["source_file"]).resolve()
    if not target.is_relative_to(paths.home().resolve()):
        raise ValueError("universe source file must be within FACTORLAB_HOME")
    if target.suffix.lower() == ".csv":
        with target.open(encoding="utf-8-sig", newline="") as handle:
            return {r["Symbol"].strip() for r in csv.DictReader(handle) if r.get("Symbol")}
    return set(yaml.safe_load(target.read_text(encoding="utf-8"))["symbols"])


def select_series(sink: IndiaSink, *, symbols: set[str] | None, today: date) -> list[Series]:
    listings = {
        r.ref: r
        for r in sink.listings
        if r.active and (symbols is None or r.ref.trading_symbol in symbols)
    }
    resolver = IdentityResolver(sink.client)
    identities = resolver.listings(listings)
    equities = [
        Series(ref, identities[ref].listing_id)
        for ref in sorted(listings, key=lambda r: (r.trading_symbol, r.alias_value))
        if ref in identities
    ]
    if not equities:
        raise RuntimeError("instrument master resolved no equities for the requested universe")
    underlying_ids = {s.listing_id for s in equities}
    nearest: dict[UUID, ContractRecord] = {}
    candidates = [
        r
        for r in sink.contracts
        if r.active and r.expiry >= today and r.product_type == "single_stock_future"
    ]
    underlying = resolver.listings(r.underlying for r in candidates)
    for row in candidates:
        identity = underlying.get(row.underlying)
        if identity is None or identity.listing_id not in underlying_ids:
            continue
        previous = nearest.get(identity.listing_id)
        if previous is None or (row.expiry, row.ref.alias_value) < (
            previous.expiry,
            previous.ref.alias_value,
        ):
            nearest[identity.listing_id] = row
    contracts = resolver.contracts(r.ref for r in nearest.values())
    futures = [
        Series(r.ref, listing_id, contracts[r.ref].contract_id)
        for listing_id, r in sorted(nearest.items(), key=lambda pair: pair[1].ref.alias_value)
        if r.ref in contracts
    ]
    return equities + futures


class Sources:
    """Share the token's rate budget across reference, equity and futures sources."""

    def __init__(self, *, batch_size: int = 100) -> None:
        configured = load_bindings().bindings
        self.bindings: dict[str, Binding] = {}
        self.settings: dict[str, UpstoxSettings] = {}
        self.clients: dict[str, UpstoxClient] = {}
        for dataset in SOURCE_CLASSES:
            matches = [
                b
                for b in configured
                if b.dataset == dataset
                and b.market == "IND"
                and b.provider == "upstox"
                and b.role == "primary"
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"India collector requires one primary Upstox binding for {dataset}"
                )
            binding = matches[0]
            self.bindings[dataset] = binding
            instance = binding.instance_name
            if instance not in self.clients:
                settings = load_provider_settings("upstox", UpstoxSettings, instance=instance)
                settings = UpstoxSettings.model_validate(
                    {**settings.model_dump(), "quote_batch_size": batch_size}
                )
                self.settings[instance] = settings
                self.clients[instance] = UpstoxClient(settings)

    def source(self, dataset: str):
        binding = self.bindings[dataset]
        instance = binding.instance_name
        return SOURCE_CLASSES[dataset](
            self.settings[instance], instance=instance, client=self.clients[instance]
        )

    def close(self) -> None:
        for client in self.clients.values():
            client.session.close()


def refresh(
    sink: IndiaSink,
    sources: Sources,
    *,
    universe: str,
    exchanges: Sequence[str] = ("NSE",),
    activate: bool = True,
) -> list[Series]:
    sink.listings.clear()
    sink.contracts.clear()
    with ingestion_run(
        sink,
        pipeline="india_reference_premarket",
        source="upstox",
        market_code="IND",
        universe=universe,
        requested_series=2 * len(exchanges),
    ) as ctx:
        for dataset in ("ref.listings", "ref.contracts"):
            outcomes = collect_binding(
                ctx,
                sources.bindings[dataset],
                sources.source(dataset),
                sink,
                ReferenceRequest("IND", params={"exchanges": list(exchanges)}),
            )
            if any(not unit.ok for unit in outcomes):
                raise RuntimeError(f"{dataset} refresh failed; expected inventory retained")
        if not activate:
            return []
        series = select_series(
            sink, symbols=universe_symbols(universe), today=datetime.now(IST).date()
        )
        sink.storage.sync_expected_india_series(
            [s.storage_key() for s in series], source="upstox", universe=universe
        )
    return series


def previous_completed_session(calendar, now: datetime) -> tuple[date, datetime]:
    today = now.astimezone(IST).date()
    sessions = calendar.sessions_in_range(
        (today - timedelta(days=30)).isoformat(), (today - timedelta(days=1)).isoformat()
    )
    if not len(sessions):
        raise RuntimeError("no completed NSE session found in the previous 30 days")
    session = sessions[-1]
    return session.date(), calendar.session_close(session).to_pydatetime().astimezone(UTC)


def recovery_windows(
    sink: IndiaSink, series: Sequence[Series], calendar, now: datetime
) -> list[SeriesWindow]:
    day, close = previous_completed_session(calendar, now)
    latest = sink.storage.latest_candle_times([s.storage_key() for s in series], before=close)
    windows = []
    for item in series:
        last = latest.get((item.listing_id, item.contract_id or UUID(int=0)))
        if last is not None and last >= close - timedelta(minutes=1):
            continue
        start_day = last.astimezone(IST).date() if last is not None else day
        start = datetime.combine(start_day, clock_time(), tzinfo=IST).astimezone(UTC)
        windows.append(SeriesWindow(item.ref, start, close))
    return windows


def collect_windows(
    sink: IndiaSink,
    sources: Sources,
    series: Sequence[Series],
    windows: Sequence[SeriesWindow],
    *,
    universe: str,
    pipeline: str,
    quote: bool = False,
    shutdown: GracefulShutdown | None = None,
    as_of: datetime | None = None,
) -> RunSummary | None:
    if not windows:
        log.warning("no India windows available for collection")
        return None
    by_ref = {s.ref: s for s in series}
    targets = []
    for dataset, futures in (("market.bars", False), ("market.futures_contract_bars", True)):
        selected = tuple(w for w in windows if bool(by_ref[w.instrument].contract_id) == futures)
        if not selected:
            continue
        binding = sources.bindings[dataset]
        request = BarRequest(
            "IND",
            "1min",
            selected,
            params={
                **binding.params,
                "mode": "quote" if quote and not futures else "candles",
                "as_of": (as_of or datetime.now(UTC)).isoformat(),
                "require_complete": True,
            },
        )
        source = sources.source(dataset)
        targets.append((binding, source, request))
    with ingestion_run(
        sink,
        pipeline=pipeline,
        source="upstox",
        market_code="IND",
        universe=universe,
        requested_series=sum(len(source.plan(request)) for _, source, request in targets),
        metadata={
            "expected_series": len(windows),
            "include_futures": any(by_ref[w.instrument].contract_id is not None for w in windows),
            "collection_method": "market_quote_ohlc_v3_batch"
            if quote
            else "intraday_candle_v3_per_series",
        },
    ) as ctx:
        for binding, source, request in targets:
            collect_binding(
                ctx,
                binding,
                source,
                sink,
                request,
                should_stop=(lambda: shutdown.triggered)
                if shutdown is not None
                else (lambda: False),
            )
    return ctx.summary()


def seconds_until_next_quote_sweep(
    *, now_epoch: float | None = None, sample_delay_seconds: float = 5.0
) -> float:
    timestamp = time.time() if now_epoch is None else now_epoch
    return 60.0 - timestamp % 60.0 + sample_delay_seconds


def main(argv: list[str] | None = None) -> int:
    configure_logging(component="ingest-india", service="ingest-india")
    parser = argparse.ArgumentParser(description="Engine-backed NSE minute collector")
    parser.add_argument("--universe", default="demo")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--quote-batch-size", type=int, default=100)
    parser.add_argument("--skip-recovery", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.quote_batch_size <= 100:
        parser.error("--quote-batch-size must be between 1 and 100")
    sources = Sources(batch_size=args.quote_batch_size)
    sink = IndiaSink(V2IndiaStorage.from_environment())
    calendar = xcals.get_calendar("XBOM")
    full = args.universe == FULL_EQUITY_UNIVERSE
    series: list[Series] = []
    configured_for: date | None = None
    recovered_for: date | None = None
    next_recovery = 0.0
    next_reference = 0.0
    last_future = float("-inf")
    try:
        sink.storage.seed_india_reference_data()
        with GracefulShutdown(log) as shutdown:

            def wait(seconds: float) -> None:
                monitoring_sleep(seconds, should_stop=lambda: shutdown.triggered)

            while not shutdown.triggered:
                now = datetime.now(UTC)
                today = now.astimezone(IST).date()
                if configured_for != today and time.monotonic() >= next_reference:
                    try:
                        series = refresh(sink, sources, universe=args.universe)
                        configured_for = today
                    except Exception:
                        if not args.daemon:
                            raise
                        log.exception("reference refresh failed; retaining prior inventory")
                        next_reference = time.monotonic() + 900
                        if not series:
                            wait(60)
                            continue
                if not series:
                    wait(60)
                    continue
                now = datetime.now(UTC)
                if (
                    not full
                    and not args.skip_recovery
                    and recovered_for != today
                    and time.monotonic() >= next_recovery
                ):
                    try:
                        windows = recovery_windows(sink, series, calendar, now)
                        recovery = (
                            collect_windows(
                                sink,
                                sources,
                                series,
                                windows,
                                universe=args.universe,
                                pipeline="india_historical_1min_recovery",
                                shutdown=shutdown,
                                as_of=now,
                            )
                            if windows
                            else None
                        )
                        recovered = not windows or (
                            recovery is not None and recovery.status == "success"
                        )
                    except Exception:
                        if not args.daemon:
                            raise
                        log.exception("historical recovery failed; retry scheduled")
                        recovered = False
                    if recovered:
                        recovered_for = today
                    else:
                        next_recovery = time.monotonic() + 900
                if not args.once:
                    if not len(calendar.sessions_in_range(today.isoformat(), today.isoformat())):
                        if not args.daemon:
                            return 10
                        wait(3600)
                        continue
                    if now.time() < OPEN:
                        wait(
                            max(
                                (
                                    datetime.combine(now.date(), OPEN, tzinfo=UTC) - now
                                ).total_seconds(),
                                1,
                            )
                        )
                        continue
                    if now.time() > CLOSE:
                        if not args.daemon:
                            return 0
                        wait(
                            max(
                                (
                                    datetime.combine(
                                        now.date() + timedelta(days=1), OPEN, tzinfo=UTC
                                    )
                                    - now
                                ).total_seconds(),
                                1,
                            )
                        )
                        continue
                include_futures = time.monotonic() - last_future >= 600
                selected = [s for s in series if s.contract_id is None or include_futures]
                opened = datetime.combine(today, clock_time(9, 15), tzinfo=IST).astimezone(UTC)
                windows = [SeriesWindow(s.ref, opened, now) for s in selected if opened < now]
                result = collect_windows(
                    sink,
                    sources,
                    series,
                    windows,
                    universe=args.universe,
                    pipeline="india_intraday_1min",
                    quote=full,
                    shutdown=shutdown,
                    as_of=now,
                )
                if (
                    result is not None
                    and include_futures
                    and all(
                        unit.ok
                        for unit in result.units
                        if unit.name.startswith("market.futures_contract_bars")
                    )
                ):
                    last_future = time.monotonic()
                if args.once or not args.daemon:
                    return 0 if result is not None and result.status == "success" else 2
                wait(seconds_until_next_quote_sweep() if full else 300)
        return 0
    finally:
        sources.close()
        sink.close()
