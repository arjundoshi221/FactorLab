"""Schwab dataset sources: ``plan -> fetch -> normalize`` (docs/architecture/07 §6).

* ``ref.listings`` by per-symbol ``/instruments`` lookup. Schwab has no master
  file, so the request carries canonical listings (``ReferenceRequest.instruments``)
  or ``params.symbols``. Production validates quote batches, preserving existing
  identities and attaching ``schwab_symbol`` aliases.
* ``market.bars`` daily and 1min from ``/pricehistory``, one symbol per unit;
  minute windows are clamped to Schwab's lookback and split into short chunks.
* US futures (F-043) from ``/quotes`` only, since ``/pricehistory`` serves none:
  roots as ``ref.listings`` (``mode: futures_roots``), dated contracts as
  ``ref.contracts`` and snapshots as ``market.futures_quotes``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, ClassVar

from factorlab.ingest.datasets import (
    BarRecord,
    BarRequest,
    Capabilities,
    ContractQuoteRecord,
    ContractRecord,
    FetchUnit,
    InstrumentRecord,
    InstrumentRef,
    ReferenceRequest,
    SharesOutstandingRecord,
)
from factorlab.ingest.provider import RawCapture
from factorlab.sources.schwab.futures import (
    FuturesRoot,
    batches,
    candidate_symbols,
    futures_ref,
    normalize_futures_contracts,
    normalize_futures_quotes,
    normalize_futures_roots,
)
from factorlab.sources.schwab.normalize import (
    normalize_instrument,
    normalize_pricehistory,
    normalize_quote_listings,
    normalize_quote_shares,
    provider_symbol,
    ref_to_metadata,
)
from factorlab.sources.schwab.settings import ALIAS_KIND, SchwabSettings
from factorlab.sources.schwab.transport import SchwabTransport

MARKETS = frozenset({"USA"})


class _SchwabSource:
    provider: ClassVar[str] = "schwab"
    settings_model: ClassVar[type[SchwabSettings]] = SchwabSettings

    def __init__(
        self,
        settings: SchwabSettings,
        *,
        instance: str = "schwab",
        transport: SchwabTransport | None = None,
    ) -> None:
        self.settings = settings
        self.instance = instance
        self._transport = transport

    @property
    def transport(self) -> SchwabTransport:
        if self._transport is None:
            self._transport = SchwabTransport(self.settings)
        return self._transport

    def _channel(self, endpoint: str) -> str:
        return f"{self.instance}:{endpoint}"


class SchwabListings(_SchwabSource):
    dataset: ClassVar[str] = "ref.listings"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=MARKETS, max_batch=50)

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        if request.params.get("mode") == "futures_roots":
            roots = [FuturesRoot.from_mapping(item) for item in request.params.get("roots", ())]
            return [
                FetchUnit(
                    f"futures_roots:{batch[0].root}..{batch[-1].root}",
                    self._channel("quotes"),
                    params={"roots": [root.to_mapping() for root in batch]},
                    instruments=tuple(root.ref() for root in batch),
                )
                for batch in batches(roots, self.settings.quote_batch_size)
            ]
        symbols = [provider_symbol(ref.trading_symbol) for ref in request.instruments]
        symbols += [provider_symbol(str(s)) for s in request.params.get("symbols", ())]
        symbols = list(dict.fromkeys(symbols))
        if request.params.get("mode") == "quotes":
            size = self.settings.quote_batch_size
            return [
                FetchUnit(
                    f"quotes:{symbols[i]}..{symbols[i + size - 1] if i + size <= len(symbols) else symbols[-1]}",
                    self._channel("quotes"),
                    params={"symbols": symbols[i : i + size]},
                    instruments=tuple(
                        InstrumentRef(ALIAS_KIND, s, "", s.replace("/", "-"), "US")
                        for s in symbols[i : i + size]
                    ),
                )
                for i in range(0, len(symbols), size)
            ]
        return [
            FetchUnit(
                f"instrument:{symbol}", self._channel("instruments"), params={"symbol": symbol}
            )
            for symbol in dict.fromkeys(symbols)
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        if "roots" in unit.params:
            roots = unit.params["roots"]
            symbols = [str(item["root"]) for item in roots]
            return self.transport.get(
                "/quotes",
                {"symbols": ",".join(symbols)},
                request_key=unit.name,
                metadata={"symbols": symbols, "roots": roots, "endpoint": "futures_roots"},
            )
        if "symbols" in unit.params:
            symbols = unit.params["symbols"]
            return self.transport.get(
                "/quotes",
                {"symbols": ",".join(symbols)},
                request_key=unit.name,
                metadata={"symbols": symbols, "endpoint": "quotes"},
            )
        symbol = str(unit.params["symbol"])
        return self.transport.get(
            "/instruments",
            {"symbol": symbol, "projection": "symbol-search"},
            request_key=unit.name,
            metadata={"symbol": symbol},
        )

    def normalize(self, capture: RawCapture) -> Sequence[InstrumentRecord]:
        if capture.metadata.get("endpoint") == "futures_roots":
            return normalize_futures_roots(capture)
        if capture.metadata.get("endpoint") == "quotes":
            return normalize_quote_listings(capture, exchanges=self.settings.exchanges)
        return normalize_instrument(capture, exchanges=self.settings.exchanges)


class SchwabSharesOutstanding(_SchwabSource):
    """``fundamentals.shares_outstanding`` from batched ``/quotes?fields=fundamental``.

    Not yet registered with the engine: it needs a recorded production capture as its
    conformance fixture first. ``universe-us`` runs it through an explicit binding.
    """

    dataset: ClassVar[str] = "fundamentals.shares_outstanding"
    capabilities: ClassVar[Capabilities] = Capabilities(
        markets=MARKETS, max_batch=50, alias_kind=ALIAS_KIND
    )

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        symbols = list(
            dict.fromkeys(provider_symbol(str(s)) for s in request.params.get("symbols", ()))
        )
        size = self.settings.quote_batch_size
        return [
            FetchUnit(
                f"shares:{batch[0]}..{batch[-1]}",
                self._channel("quotes"),
                params={"symbols": batch},
            )
            for batch in (symbols[i : i + size] for i in range(0, len(symbols), size))
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        symbols = unit.params["symbols"]
        return self.transport.get(
            "/quotes",
            {"symbols": ",".join(symbols), "fields": "fundamental"},
            request_key=unit.name,
            metadata={"symbols": symbols, "endpoint": "quotes"},
        )

    def normalize(self, capture: RawCapture) -> Sequence[SharesOutstandingRecord]:
        return normalize_quote_shares(capture)


class SchwabBars(_SchwabSource):
    dataset: ClassVar[str] = "market.bars"
    capabilities: ClassVar[Capabilities] = Capabilities(
        markets=MARKETS,
        resolutions=frozenset({"daily", "1min"}),
        alias_kind=ALIAS_KIND,
    )

    def plan(self, request: BarRequest) -> Sequence[FetchUnit]:
        if request.resolution not in self.capabilities.resolutions:
            raise ValueError(f"Schwab bars serve daily/1min, not {request.resolution}")
        units: list[FetchUnit] = []
        for window in request.series:
            ref = window.instrument
            if request.resolution == "daily":
                units.append(self._unit(ref, "daily", window.start, window.end))
                continue
            start = max(
                window.start, window.end - timedelta(days=self.settings.minute_max_lookback_days)
            )
            step = timedelta(days=self.settings.minute_chunk_days)
            while start < window.end:
                stop = min(start + step, window.end)
                units.append(self._unit(ref, "1min", start, stop))
                start = stop
        return [
            replace(
                unit,
                params={
                    **unit.params,
                    "require_complete": bool(request.params.get("require_complete", False)),
                },
            )
            for unit in units
        ]

    def _unit(self, ref: Any, resolution: str, start: Any, end: Any) -> FetchUnit:
        return FetchUnit(
            f"{ref.alias_value}:{resolution}:{start.isoformat()}..{end.isoformat()}",
            self._channel("pricehistory"),
            params={"resolution": resolution},
            instruments=(ref,),
            start=start,
            end=end,
        )

    def fetch(self, unit: FetchUnit) -> RawCapture:
        ref = unit.instruments[0]
        daily = unit.params["resolution"] == "daily"
        params = {
            "symbol": ref.alias_value,
            "periodType": "year" if daily else "day",
            "frequencyType": "daily" if daily else "minute",
            "frequency": 1,
            "startDate": int(unit.start.timestamp() * 1000),
            "endDate": int(unit.end.timestamp() * 1000),
            "needExtendedHoursData": "false",
        }
        return self.transport.get(
            "/pricehistory",
            params,
            request_key=unit.name,
            metadata={
                "resolution": unit.params["resolution"],
                "instruments": [ref_to_metadata(ref)],
                "require_complete": unit.params.get("require_complete", False),
            },
        )

    def normalize(self, capture: RawCapture) -> Sequence[BarRecord]:
        return normalize_pricehistory(capture)


class SchwabFuturesContracts(_SchwabSource):
    """``ref.contracts`` for US futures roots from batched ``/quotes`` (F-043).

    ``params.roots`` holds roster entries and ``params.as_of`` the date the cycle
    starts from (the caller's clock, so ``plan`` stays pure). Each unit quotes whole
    roots: a root with its next contracts never spans two captures. Not registered
    until a recorded production capture backs its conformance fixture.
    """

    dataset: ClassVar[str] = "ref.contracts"
    capabilities: ClassVar[Capabilities] = Capabilities(
        markets=MARKETS, max_batch=50, alias_kind=ALIAS_KIND
    )

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        as_of = date.fromisoformat(str(request.params["as_of"]))
        roots = [FuturesRoot.from_mapping(item) for item in request.params.get("roots", ())]
        units: list[FetchUnit] = []
        batch: list[FuturesRoot] = []
        size = 0
        for root in [*roots, None]:
            wanted = len(candidate_symbols(root, as_of)) if root else 0
            if batch and (root is None or size + wanted > self.settings.quote_batch_size):
                symbols = [s for item in batch for s in candidate_symbols(item, as_of)]
                units.append(
                    FetchUnit(
                        f"futures_contracts:{batch[0].root}..{batch[-1].root}",
                        self._channel("quotes"),
                        params={"symbols": symbols, "roots": [r.to_mapping() for r in batch]},
                        instruments=tuple(r.ref() for r in batch),
                    )
                )
                batch, size = [], 0
            if root is not None:
                batch.append(root)
                size += wanted
        return units

    def fetch(self, unit: FetchUnit) -> RawCapture:
        symbols = unit.params["symbols"]
        return self.transport.get(
            "/quotes",
            {"symbols": ",".join(symbols)},
            request_key=unit.name,
            metadata={"symbols": symbols, "roots": unit.params["roots"], "endpoint": "quotes"},
        )

    def normalize(self, capture: RawCapture) -> Sequence[ContractRecord]:
        return normalize_futures_contracts(capture)


class SchwabFuturesQuotes(_SchwabSource):
    """``market.futures_quotes`` from batched ``/quotes``; ``params.contracts`` maps symbol -> MIC.

    Not registered until a recorded production capture backs its conformance fixture.
    """

    dataset: ClassVar[str] = "market.futures_quotes"
    capabilities: ClassVar[Capabilities] = Capabilities(
        markets=MARKETS, max_batch=50, alias_kind=ALIAS_KIND, polling=True
    )

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        contracts = {str(k).upper(): str(v) for k, v in request.params.get("contracts", {}).items()}
        symbols = sorted(contracts)
        return [
            FetchUnit(
                f"futures_quotes:{batch[0]}..{batch[-1]}",
                self._channel("quotes"),
                params={"contracts": {symbol: contracts[symbol] for symbol in batch}},
                instruments=tuple(futures_ref(symbol, contracts[symbol]) for symbol in batch),
            )
            for batch in batches(symbols, self.settings.quote_batch_size)
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        contracts = unit.params["contracts"]
        return self.transport.get(
            "/quotes",
            {"symbols": ",".join(contracts)},
            request_key=unit.name,
            metadata={"contracts": contracts, "endpoint": "quotes"},
        )

    def normalize(self, capture: RawCapture) -> Sequence[ContractQuoteRecord]:
        return normalize_futures_quotes(capture)


__all__ = [
    "SchwabBars",
    "SchwabFuturesContracts",
    "SchwabFuturesQuotes",
    "SchwabListings",
    "SchwabSharesOutstanding",
]
