"""Schwab dataset sources: ``plan -> fetch -> normalize`` (docs/architecture/07 §6).

* ``ref.listings`` by per-symbol ``/instruments`` lookup. Schwab has no master
  file, so the request carries canonical listings (``ReferenceRequest.instruments``)
  or ``params.symbols``; Schwab is meant to run as ``secondary`` here, attaching
  its ``schwab_symbol`` aliases to listings another provider owns.
* ``market.bars`` daily and 1min from ``/pricehistory``, one symbol per unit;
  minute windows are clamped to Schwab's lookback and split into short chunks.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any, ClassVar

from factorlab.ingest.datasets import (
    BarRecord,
    BarRequest,
    Capabilities,
    FetchUnit,
    InstrumentRecord,
    ReferenceRequest,
)
from factorlab.ingest.provider import RawCapture
from factorlab.sources.schwab.normalize import (
    normalize_instrument,
    normalize_pricehistory,
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
    capabilities: ClassVar[Capabilities] = Capabilities(markets=MARKETS)

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        symbols = [provider_symbol(ref.trading_symbol) for ref in request.instruments]
        symbols += [provider_symbol(str(s)) for s in request.params.get("symbols", ())]
        return [
            FetchUnit(
                f"instrument:{symbol}", self._channel("instruments"), params={"symbol": symbol}
            )
            for symbol in dict.fromkeys(symbols)
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        symbol = str(unit.params["symbol"])
        return self.transport.get(
            "/instruments",
            {"symbol": symbol, "projection": "symbol-search"},
            request_key=unit.name,
            metadata={"symbol": symbol},
        )

    def normalize(self, capture: RawCapture) -> Sequence[InstrumentRecord]:
        return normalize_instrument(capture, exchanges=self.settings.exchanges)


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
        return units

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
            },
        )

    def normalize(self, capture: RawCapture) -> Sequence[BarRecord]:
        return normalize_pricehistory(capture)


__all__ = ["SchwabBars", "SchwabListings"]
