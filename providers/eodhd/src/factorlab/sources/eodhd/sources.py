"""EODHD dataset sources: ``plan -> fetch -> normalize`` (docs/architecture/07 §6).

* ``ref.listings`` from ``/exchange-symbol-list/US`` (one call, whole exchange).
* ``market.bars`` daily, in two modes chosen by the binding's ``params.mode``:

  - ``history`` (default): ``/eod/{CODE}.US?from&to`` per instrument window;
  - ``bulk``: ``/eod-bulk-last-day/US?date=`` once per session date, covering
    every requested instrument (EODHD bills a bulk call as 100 requests).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any, ClassVar
from urllib.parse import quote

from factorlab.calendars.us import NY, is_session
from factorlab.ingest.datasets import (
    BarRecord,
    BarRequest,
    Capabilities,
    ConstituentRecord,
    FetchUnit,
    InstrumentRecord,
    ReferenceRequest,
)
from factorlab.ingest.provider import RawCapture
from factorlab.sources.eodhd.client import EodhdClient
from factorlab.sources.eodhd.normalize import (
    normalize_bulk,
    normalize_components,
    normalize_eod,
    normalize_listings,
    ref_to_metadata,
)
from factorlab.sources.eodhd.settings import ALIAS_KIND, EodhdSettings

MARKETS = frozenset({"USA"})
BULK_BATCH = 100_000  # the bulk endpoint returns the whole exchange in one response


class _EodhdSource:
    provider: ClassVar[str] = "eodhd"
    settings_model: ClassVar[type[EodhdSettings]] = EodhdSettings

    def __init__(
        self, settings: EodhdSettings, *, instance: str = "eodhd", client: EodhdClient | None = None
    ) -> None:
        self.settings = settings
        self.instance = instance
        self._client = client

    @property
    def client(self) -> EodhdClient:
        if self._client is None:
            self._client = EodhdClient(self.settings)
        return self._client

    def _channel(self, endpoint: str) -> str:
        return f"{self.instance}:{endpoint}"


class EodhdListings(_EodhdSource):
    dataset: ClassVar[str] = "ref.listings"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=MARKETS)

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        exchange = str(request.params.get("exchange") or self.settings.default_exchange)
        return [
            FetchUnit(
                f"symbols:{exchange}",
                self._channel("exchange_symbol_list"),
                params={"exchange": exchange},
            )
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        exchange = str(unit.params["exchange"])
        return self.client.get(
            f"/exchange-symbol-list/{quote(exchange)}",
            request_key=unit.name,
            metadata={"exchange": exchange},
        )

    def normalize(self, capture: RawCapture) -> Sequence[InstrumentRecord]:
        return normalize_listings(
            capture, venues=self.settings.venues, listing_types=self.settings.listing_types
        )


class EodhdUniverse(_EodhdSource):
    dataset: ClassVar[str] = "ref.universe_membership"
    capabilities: ClassVar[Capabilities] = Capabilities(markets=MARKETS)

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        codes = request.params.get("universes") or sorted(self.settings.universes)
        unknown = sorted(set(codes) - set(self.settings.universes))
        if unknown:
            raise ValueError(f"no eodhd universe configured for {unknown}")
        return [
            FetchUnit(
                f"universe:{code}", self._channel("index_components"), params={"universe": code}
            )
            for code in codes
        ]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        code = str(unit.params["universe"])
        index = self.settings.universes[code]
        return self.client.get(
            f"/fundamentals/{quote(index.symbol)}",
            {"filter": "Components"},
            request_key=unit.name,
            metadata={"universe": code, **index.model_dump()},
        )

    def normalize(self, capture: RawCapture) -> Sequence[ConstituentRecord]:
        return normalize_components(capture)


class EodhdDailyBars(_EodhdSource):
    dataset: ClassVar[str] = "market.bars"
    capabilities: ClassVar[Capabilities] = Capabilities(
        markets=MARKETS,
        resolutions=frozenset({"daily"}),
        alias_kind=ALIAS_KIND,
        max_batch=BULK_BATCH,
    )

    def plan(self, request: BarRequest) -> Sequence[FetchUnit]:
        if request.resolution != "daily":
            raise ValueError(f"EODHD bars serve daily, not {request.resolution}")
        mode = request.params.get("mode", "history")
        if mode == "bulk":
            return self._plan_bulk(request)
        if mode != "history":
            raise ValueError(f"unknown EODHD bar mode {mode!r}")
        units = []
        for window in request.series:
            first = window.start.astimezone(NY).date()
            last = window.end.astimezone(NY).date()
            units.append(
                FetchUnit(
                    f"{window.instrument.alias_value}:{first}..{last}",
                    self._channel("eod"),
                    params={"endpoint": "eod", "from": first.isoformat(), "to": last.isoformat()},
                    instruments=(window.instrument,),
                    start=window.start,
                    end=window.end,
                )
            )
        return units

    def _plan_bulk(self, request: BarRequest) -> list[FetchUnit]:
        if not request.series:
            return []
        refs = tuple(dict.fromkeys(window.instrument for window in request.series))
        last = max(window.end for window in request.series).astimezone(NY).date()
        first = max(
            min(window.start for window in request.series).astimezone(NY).date(),
            last - timedelta(days=self.settings.bulk_max_days - 1),
        )
        units = []
        day = first
        while day <= last:
            if is_session(day):
                units.append(
                    FetchUnit(
                        f"bulk:{self.settings.default_exchange}:{day}",
                        self._channel("eod_bulk"),
                        params={"endpoint": "bulk", "date": day.isoformat()},
                        instruments=refs,
                    )
                )
            day += timedelta(days=1)
        return units

    def fetch(self, unit: FetchUnit) -> RawCapture:
        metadata: dict[str, Any] = {
            "endpoint": unit.params["endpoint"],
            "resolution": "daily",
            "instruments": [ref_to_metadata(ref) for ref in unit.instruments],
        }
        if unit.params["endpoint"] == "bulk":
            exchange = self.settings.default_exchange
            return self.client.get(
                f"/eod-bulk-last-day/{quote(exchange)}",
                {"date": unit.params["date"]},
                request_key=unit.name,
                metadata=metadata,
            )
        symbol = unit.instruments[0].alias_value
        return self.client.get(
            f"/eod/{quote(symbol)}",
            {"from": unit.params["from"], "to": unit.params["to"]},
            request_key=unit.name,
            metadata=metadata,
        )

    def normalize(self, capture: RawCapture) -> Sequence[BarRecord]:
        if capture.metadata.get("endpoint") == "bulk":
            return normalize_bulk(capture)
        return normalize_eod(capture)


__all__ = ["EodhdDailyBars", "EodhdListings", "EodhdUniverse"]
