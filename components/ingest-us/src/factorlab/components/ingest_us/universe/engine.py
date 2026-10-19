"""Stage complete index snapshots until Schwab validates the entire union."""

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from factorlab.components.ingest_us.constituent_csv import ConstituentCsvUniverseResolver
from factorlab.components.ingest_us.engine_client import EngineClient
from factorlab.components.ingest_us.universe.base import UniverseResolver
from factorlab.components.ingest_us.universe.config import UniverseConfig
from factorlab.components.ingest_us.universe.models import ResolvedConstituent
from factorlab.ingest.bindings import Binding, load_provider_settings
from factorlab.ingest.datasets import (
    ConstituentRecord,
    InstrumentRecord,
    InstrumentRef,
    ReferenceRequest,
    WriteResult,
)
from factorlab.ingest.datasets.reference import ReferenceMode
from factorlab.ingest.engine import collect_binding
from factorlab.ingest.provider import Provenance, RunContext
from factorlab.sources.eodhd.settings import EodhdIndex, EodhdSettings
from factorlab.sources.eodhd.sources import EodhdUniverse
from factorlab.sources.github_csv.sources import CsvIndex, GithubCsvSettings, GithubCsvUniverse
from factorlab.storage.sinks import ClickHouseSinks


class RosterSink(ClickHouseSinks):
    def __init__(self, storage: Any) -> None:
        super().__init__(storage)
        self.snapshots: list[tuple[tuple[ConstituentRecord, ...], Provenance]] = []
        self.listings: list[InstrumentRecord] = []

    def write_constituents(
        self,
        rows: Sequence[ConstituentRecord],
        *,
        provenance: Provenance,
        mode: ReferenceMode = "authoritative",
    ) -> WriteResult:
        self.snapshots.append((tuple(rows), provenance))
        return WriteResult(0, 0, len(rows))

    def upsert_instruments(
        self,
        rows: Sequence[InstrumentRecord],
        *,
        provenance: Provenance,
        mode: ReferenceMode = "authoritative",
    ) -> WriteResult:
        result = super().upsert_instruments(rows, provenance=provenance, mode=mode)
        self.listings.extend(rows)
        return result

    def publish(self, references: Sequence[InstrumentRef]) -> None:
        by_symbol = {ref.trading_symbol: ref for ref in references}
        snapshots = [
            (
                tuple(
                    replace(row, instrument=by_symbol[row.instrument.trading_symbol])
                    for row in rows
                ),
                provenance,
            )
            for rows, provenance in self.snapshots
        ]
        for rows, provenance in snapshots:
            result = super().write_constituents(rows, provenance=provenance)
            if result.unresolved:
                raise ValueError("validated index has unresolved canonical listings")


class EngineValidator:
    def __init__(self, client: EngineClient, ctx: RunContext, sink: RosterSink) -> None:
        self.client, self.ctx, self.sink = client, ctx, sink

    def validate(self, symbols: Iterable[str]) -> list[ResolvedConstituent]:
        self.sink.listings.clear()
        self.client.resolve_symbols(self.ctx, self.sink, list(symbols))
        return [
            ResolvedConstituent(
                symbol=row.ref.trading_symbol,
                name=row.name,
                exchange=row.ref.exchange_code,
                currency=row.currency,
                instrument_type="EQUITY",
            )
            for row in self.sink.listings
        ]


class DatasetUniverseResolver(UniverseResolver):
    def __init__(
        self,
        *,
        provider: str,
        adapter: Any,
        validator: EngineValidator,
        ctx: RunContext,
        sink: RosterSink,
    ) -> None:
        super().__init__(validator=validator)
        self.provider, self.adapter, self.ctx, self.sink = provider, adapter, ctx, sink
        self.sleep = validator.client.wait
        # The mounted universe config explicitly selects its membership authority.
        # This job never runs alternate configured providers as implicit fallbacks.
        self.binding = Binding(dataset="ref.universe_membership", market="USA", provider=provider)

    def _retrieve_index(self, name: str) -> Iterable[str]:
        before = len(self.sink.snapshots)
        outcomes = collect_binding(
            self.ctx,
            self.binding,
            self.adapter,
            self.sink,
            ReferenceRequest("USA", params={"universes": [name]}),
            sleep=self.sleep,
        )
        if not outcomes or any(not u.ok for u in outcomes):
            raise ValueError(f"{name}: index retrieval or normalization failed")
        snapshots = self.sink.snapshots[before:]
        self._provenance[name] = [str(p.raw_id) for _, p in snapshots]
        return [r.instrument.trading_symbol for rows, _ in snapshots for r in rows]

    def close(self) -> None:
        if self.provider == "github_csv":
            self.adapter.session.close()
        elif self.provider == "eodhd":
            self.adapter.client.session.close()


def create_resolver(
    config: UniverseConfig, storage: Any, *, client: EngineClient, ctx: RunContext, sink: RosterSink
) -> UniverseResolver:
    validator = EngineValidator(client, ctx, sink)
    mapping = getattr(config.providers, config.provider)
    if config.provider == "constituent_csv":
        return ConstituentCsvUniverseResolver(
            indexes=mapping.indexes, validator=validator, storage=storage
        )
    minimums = {i.name: i.minimum_constituents for i in config.indexes}
    if config.provider == "github_csv":
        settings = GithubCsvSettings(
            indexes={
                name: CsvIndex(
                    url=str(item.url),
                    symbol_column=item.symbol_column,
                    minimum_constituents=minimums[name],
                )
                for name, item in mapping.indexes.items()
                if name in minimums
            }
        )
        adapter = GithubCsvUniverse(settings)
    else:
        configured = load_provider_settings("eodhd", EodhdSettings)
        settings = EodhdSettings.model_validate(
            {
                **configured.model_dump(),
                "universes": {
                    name: EodhdIndex(symbol=item.symbol, minimum_constituents=minimums[name])
                    for name, item in mapping.indexes.items()
                    if name in minimums
                },
            }
        )
        adapter = EodhdUniverse(settings)
    return DatasetUniverseResolver(
        provider=config.provider, adapter=adapter, validator=validator, ctx=ctx, sink=sink
    )
