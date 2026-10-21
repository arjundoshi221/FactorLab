"""US collection jobs use dataset sources and canonical sinks under one run."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from factorlab.components.ingest_us.settings import CollectionSettings
from factorlab.ingest.bindings import Binding, load_bindings, load_provider_settings
from factorlab.ingest.datasets import BarRecord, InstrumentRef, ReferenceRequest, WriteResult
from factorlab.ingest.engine import collect_binding
from factorlab.ingest.provider import Provenance, RunContext, UnitOutcome, ingestion_run
from factorlab.ingest.ratelimit import SlidingWindowLimiter
from factorlab.runtime.monitor import monitoring_sleep
from factorlab.sources.schwab.normalize import provider_symbol
from factorlab.sources.schwab.settings import ALIAS_KIND, SchwabSettings
from factorlab.sources.schwab.sources import (
    SchwabBars,
    SchwabFuturesContracts,
    SchwabFuturesQuotes,
    SchwabListings,
    SchwabSharesOutstanding,
)
from factorlab.sources.schwab.transport import SchwabTransport
from factorlab.storage.sinks import ClickHouseSinks
from factorlab.storage.sinks.identity import IdentityResolver


class PriceSink(ClickHouseSinks):
    """Retain accepted bars for recovery state and coverage bookkeeping."""

    def __init__(self, storage: Any) -> None:
        super().__init__(storage)
        self.bars: list[BarRecord] = []

    def write_bars(self, rows: Sequence[BarRecord], *, provenance: Provenance) -> WriteResult:
        result = super().write_bars(rows, provenance=provenance)
        if result.unresolved:
            raise ValueError("price response contains unresolved listing identities")
        self.bars.extend(rows)
        return result


class EngineClient:
    """Share one transport, credential refresh path and rate budget for a collector."""

    def __init__(self, *, transport: SchwabTransport | None = None) -> None:
        self.bindings: dict[tuple[str, str | None], Binding] = {}
        configured = load_bindings()
        for dataset, resolution in (
            ("ref.listings", None),
            ("market.bars", "daily"),
            ("market.bars", "1min"),
        ):
            matches = [
                b
                for b in configured.bindings
                if b.provider == "schwab"
                and b.market == "USA"
                and b.dataset == dataset
                and b.resolution == resolution
                and b.role == "primary"
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"US collector requires one primary Schwab binding for {dataset}/{resolution}"
                )
            self.bindings[dataset, resolution] = matches[0]
        listing = self.bindings["ref.listings", None]
        instances = {b.instance_name for b in self.bindings.values()}
        if len(instances) != 1:
            raise ValueError("production Schwab bindings must share one credential instance")
        settings = load_provider_settings("schwab", SchwabSettings, instance=listing.instance_name)
        self.settings = SchwabSettings.model_validate(settings.model_dump())
        runtime = CollectionSettings()
        windows = (
            *self.settings.rate_limits.windows(),
            (runtime.schwab_min_request_interval_seconds, 1),
        )
        # One limiter per pooled account: each Schwab app has its own budget.
        self.transport = transport or SchwabTransport(
            self.settings,
            limiter_factory=lambda: SlidingWindowLimiter(windows, sleep=self.wait),
        )

    def token_ready(self, now: datetime | None = None) -> bool:
        return self.transport.token_ready(now)

    @staticmethod
    def wait(seconds: float) -> None:
        monitoring_sleep(seconds, should_stop=lambda: False)

    def bars_source(self) -> SchwabBars:
        binding = self.bindings["market.bars", "daily"]
        return SchwabBars(self.settings, instance=binding.instance_name, transport=self.transport)

    def listing_source(self) -> SchwabListings:
        binding = self.bindings["ref.listings", None]
        return SchwabListings(
            self.settings, instance=binding.instance_name, transport=self.transport
        )

    def resolve_symbols(
        self,
        ctx: RunContext,
        sink: ClickHouseSinks,
        symbols: Sequence[str],
        *,
        allow_partial: bool = False,
    ) -> None:
        """Validate ``symbols``; with ``allow_partial`` the caller checks what validated."""
        binding: Binding = self.bindings["ref.listings", None]
        outcomes = collect_binding(
            ctx,
            binding,
            self.listing_source(),
            sink,
            ReferenceRequest("USA", params={"mode": "quotes", "symbols": list(symbols)}),
        )
        if not allow_partial and any(not outcome.ok for outcome in outcomes):
            raise ValueError("Schwab could not validate the full configured equity roster")

    def collect_shares(self, ctx: RunContext, sink: ClickHouseSinks, symbols: Sequence[str]) -> int:
        """Snapshot shares outstanding for ``symbols``; returns the rows written."""
        listing = self.bindings["ref.listings", None]
        source = SchwabSharesOutstanding(
            self.settings, instance=listing.instance_name, transport=self.transport
        )
        binding = Binding(
            dataset="fundamentals.shares_outstanding",
            market="USA",
            provider="schwab",
            instance=listing.instance,
        )
        outcomes = collect_binding(
            ctx, binding, source, sink, ReferenceRequest("USA", params={"symbols": list(symbols)})
        )
        return sum(outcome.rows_written for outcome in outcomes)

    # -- US futures (F-043) ------------------------------------------------------
    def _futures_binding(self, dataset: str) -> Binding:
        # Futures sources are unregistered until recorded fixtures exist, so their
        # bindings are explicit and share the listing binding's credential instance.
        listing = self.bindings["ref.listings", None]
        return Binding(dataset=dataset, market="USA", provider="schwab", instance=listing.instance)

    def collect_futures_roster(
        self,
        ctx: RunContext,
        sink: ClickHouseSinks,
        roots: Sequence[Mapping[str, Any]],
        as_of: date,
    ) -> tuple[UnitOutcome, ...]:
        """Root listings, then the contracts under them, inside the caller's run."""
        listing = self.bindings["ref.listings", None]
        roots = [dict(root) for root in roots]
        outcomes = collect_binding(
            ctx,
            listing,
            self.listing_source(),
            sink,
            ReferenceRequest("USA", params={"mode": "futures_roots", "roots": roots}),
        )
        source = SchwabFuturesContracts(
            self.settings, instance=listing.instance_name, transport=self.transport
        )
        return outcomes + collect_binding(
            ctx,
            self._futures_binding("ref.contracts"),
            source,
            sink,
            ReferenceRequest("USA", params={"roots": roots, "as_of": as_of.isoformat()}),
        )

    def collect_futures_quotes(
        self, ctx: RunContext, sink: ClickHouseSinks, contracts: Mapping[str, str]
    ) -> tuple[UnitOutcome, ...]:
        """Snapshot ``contracts`` (symbol -> MIC) into ``market.futures_quotes``."""
        listing = self.bindings["ref.listings", None]
        source = SchwabFuturesQuotes(
            self.settings, instance=listing.instance_name, transport=self.transport
        )
        return collect_binding(
            ctx,
            self._futures_binding("market.futures_quotes"),
            source,
            sink,
            ReferenceRequest("USA", params={"contracts": dict(contracts)}),
        )

    def configure_pilot(
        self, storage: Any, symbols: Sequence[str], universe: str
    ) -> list[tuple[str, str, UUID]]:
        sink = ClickHouseSinks(storage)
        with ingestion_run(
            sink, pipeline="us_universe_sync", source="schwab", market_code="USA", universe=universe
        ) as ctx:
            self.resolve_symbols(ctx, sink, symbols)
            # Resolve through canonical aliases; never derive replacement IDs.
            refs = [
                InstrumentRef(ALIAS_KIND, provider_symbol(symbol), "", symbol, "US")
                for symbol in symbols
            ]
            identities = IdentityResolver(sink.client).listings(refs)
            if len(identities) != len(refs):
                raise ValueError("validated pilot roster contains unresolved identities")
            items = [
                (ref.trading_symbol, ref.alias_value, identities[ref].listing_id) for ref in refs
            ]
            storage.sync_expected_series(
                [
                    {"symbol": symbol, "provider_symbol": provider, "instrument_id": identifier}
                    for symbol, provider, identifier in items
                ],
                source="schwab",
                universe=universe,
                resolution="daily",
            )
        return items

    def close(self) -> None:
        self.transport.session.close()
