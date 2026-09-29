"""Upstox dataset sources: ``plan -> fetch -> normalize`` (docs/architecture/07 §6).

* ``ref.listings`` / ``ref.contracts`` from the public instrument master
  (one gzip download per exchange, no auth).
* ``market.bars`` / ``market.futures_contract_bars`` at 1min, in two modes
  chosen by the binding's ``params.mode``:

  - ``candles`` (default): V3 historical windows for past IST days plus the
    V3 intraday endpoint for today, one instrument per unit;
  - ``quote``: the V3 OHLC quote for up to 100 instruments per unit, which
    returns the last finalized minute (the full-NSE live path).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, ClassVar
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from factorlab.shared.ingest.datasets import (
    BarRecord,
    BarRequest,
    Capabilities,
    ContractBarRecord,
    ContractRecord,
    FetchUnit,
    InstrumentRecord,
    ReferenceRequest,
)
from factorlab.shared.ingest.provider import RawCapture
from factorlab.sources.upstox.client import UpstoxClient
from factorlab.sources.upstox.normalize import (
    normalize_bars,
    normalize_contracts,
    normalize_listings,
    ref_to_metadata,
)
from factorlab.sources.upstox.settings import ALIAS_KIND, UpstoxSettings

IST = ZoneInfo("Asia/Kolkata")
MARKETS = frozenset({"IND"})


def _ist_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=IST).astimezone(UTC)


def historical_windows(start: date, end: date, *, available_from: date,
                       chunk_days: int) -> list[tuple[date, date]]:
    """Inclusive IST-date ranges accepted by the V3 minute history endpoint."""
    cursor = max(start, available_from)
    windows: list[tuple[date, date]] = []
    while cursor <= end:
        stop = min(cursor + timedelta(days=chunk_days - 1), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


class _UpstoxSource:
    provider: ClassVar[str] = "upstox"
    settings_model: ClassVar[type[UpstoxSettings]] = UpstoxSettings

    def __init__(self, settings: UpstoxSettings, *, instance: str = "upstox",
                 client: UpstoxClient | None = None) -> None:
        self.settings = settings
        self.instance = instance
        self._client = client

    @property
    def client(self) -> UpstoxClient:
        if self._client is None:
            self._client = UpstoxClient(self.settings)
        return self._client

    def _channel(self, endpoint: str) -> str:
        return f"{self.instance}:{endpoint}"


class _InstrumentMaster(_UpstoxSource):
    capabilities: ClassVar[Capabilities] = Capabilities(markets=MARKETS)

    def plan(self, request: ReferenceRequest) -> Sequence[FetchUnit]:
        exchanges = request.params.get("exchanges") or self.settings.exchanges
        return [FetchUnit(f"master:{exchange}", self._channel("instruments"),
                          params={"exchange": exchange}) for exchange in exchanges]

    def fetch(self, unit: FetchUnit) -> RawCapture:
        exchange = str(unit.params["exchange"])
        url = self.settings.instruments_url.format(exchange=exchange)
        return self.client.get(url, request_key=unit.name, auth=False,
                               metadata={"exchange": exchange})


class UpstoxListings(_InstrumentMaster):
    dataset: ClassVar[str] = "ref.listings"

    def normalize(self, capture: RawCapture) -> Sequence[InstrumentRecord]:
        return normalize_listings(capture,
                                  instrument_types=self.settings.listing_instrument_types)


class UpstoxContracts(_InstrumentMaster):
    dataset: ClassVar[str] = "ref.contracts"

    def normalize(self, capture: RawCapture) -> Sequence[ContractRecord]:
        return normalize_contracts(capture,
                                   underlying_types=self.settings.contract_underlying_types)


class _UpstoxBars(_UpstoxSource):
    capabilities: ClassVar[Capabilities] = Capabilities(
        markets=MARKETS, resolutions=frozenset({"1min"}), alias_kind=ALIAS_KIND,
        max_batch=100, polling=True,
    )
    contract: ClassVar[bool] = False

    def plan(self, request: BarRequest) -> Sequence[FetchUnit]:
        if request.resolution != "1min":
            raise ValueError(f"Upstox bars serve 1min, not {request.resolution}")
        mode = request.params.get("mode", "candles")
        if mode == "quote":
            return self._plan_quotes(request)
        if mode != "candles":
            raise ValueError(f"unknown Upstox bar mode {mode!r}")
        units: list[FetchUnit] = []
        for window in request.series:
            ref = window.instrument
            today = window.end.astimezone(IST).date()
            first = window.start.astimezone(IST).date()
            for start, stop in historical_windows(
                    first, today - timedelta(days=1),
                    available_from=self.settings.history_available_from,
                    chunk_days=self.settings.historical_chunk_days):
                units.append(FetchUnit(
                    f"{ref.alias_value}:{start}..{stop}", self._channel("v3_historical"),
                    params={"endpoint": "historical", "from": start.isoformat(),
                            "to": stop.isoformat()},
                    instruments=(ref,), start=_ist_midnight(start),
                    end=_ist_midnight(stop + timedelta(days=1)),
                ))
            today_start = _ist_midnight(today)
            if window.end > today_start:
                units.append(FetchUnit(
                    f"{ref.alias_value}:intraday:{today}", self._channel("v3_intraday"),
                    params={"endpoint": "intraday"}, instruments=(ref,),
                    start=max(window.start, today_start), end=window.end,
                ))
        return units

    def _plan_quotes(self, request: BarRequest) -> list[FetchUnit]:
        refs = list(dict.fromkeys(window.instrument for window in request.series))
        size = self.settings.quote_batch_size
        units = []
        for index in range(0, len(refs), size):
            batch = tuple(refs[index:index + size])
            units.append(FetchUnit(
                f"quote:{batch[0].alias_value}..{batch[-1].alias_value}",
                self._channel("v3_quote_ohlc"), params={"endpoint": "quote"}, instruments=batch,
            ))
        return units

    def _url(self, unit: FetchUnit) -> str:
        base = self.settings.api.base_url.rstrip("/")
        endpoint = unit.params["endpoint"]
        if endpoint == "quote":
            keys = ",".join(ref.alias_value for ref in unit.instruments)
            return f"{base}/v3/market-quote/ohlc?{urlencode({'instrument_key': keys, 'interval': 'I1'})}"
        key = quote(unit.instruments[0].alias_value, safe="")
        if endpoint == "intraday":
            return f"{base}/v3/historical-candle/intraday/{key}/minutes/1"
        return f"{base}/v3/historical-candle/{key}/minutes/1/{unit.params['to']}/{unit.params['from']}"

    def fetch(self, unit: FetchUnit) -> RawCapture:
        metadata: dict[str, Any] = {
            "endpoint": unit.params["endpoint"], "resolution": "1min",
            "instruments": [ref_to_metadata(ref) for ref in unit.instruments],
        }
        for key in ("from", "to"):
            if key in unit.params:
                metadata[key] = unit.params[key]
        return self.client.get(self._url(unit), request_key=unit.name, metadata=metadata)

    def normalize(self, capture: RawCapture) -> Sequence[Any]:
        return normalize_bars(capture, contract=self.contract)


class UpstoxBars(_UpstoxBars):
    dataset: ClassVar[str] = "market.bars"

    def normalize(self, capture: RawCapture) -> Sequence[BarRecord]:
        return normalize_bars(capture, contract=False)  # type: ignore[return-value]


class UpstoxContractBars(_UpstoxBars):
    dataset: ClassVar[str] = "market.futures_contract_bars"
    contract: ClassVar[bool] = True

    def normalize(self, capture: RawCapture) -> Sequence[ContractBarRecord]:
        return normalize_bars(capture, contract=True)  # type: ignore[return-value]


__all__ = [
    "UpstoxBars",
    "UpstoxContractBars",
    "UpstoxContracts",
    "UpstoxListings",
    "historical_windows",
]
