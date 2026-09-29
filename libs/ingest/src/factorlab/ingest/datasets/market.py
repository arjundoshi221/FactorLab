"""``market.bars`` and ``market.futures_contract_bars`` dataset contracts (docs/architecture/07 §5.3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from factorlab.ingest.datasets.common import (
    RESOLUTIONS,
    SESSIONS,
    InstrumentRef,
    WriteResult,
    require_decimal,
    require_int,
    require_utc,
)
from factorlab.ingest.provider import Provenance, ProviderStorage

_PRICE_FIELDS = ("open", "high", "low", "close")


def _validate_bar(bar: BarRecord | ContractBarRecord) -> None:
    if bar.resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution {bar.resolution!r}")
    require_utc("bar_time", bar.bar_time)
    for name in (*_PRICE_FIELDS, "turnover", "settlement_price"):
        require_decimal(name, getattr(bar, name))
    for name in ("volume", "trades_count", "oi"):
        value = getattr(bar, name)
        require_int(name, value)
        if value is not None and value < 0:
            raise ValueError(f"{name} must not be negative, got {value}")
    if bar.session is not None and bar.session not in SESSIONS:
        raise ValueError(f"unknown session {bar.session!r}")
    o, h, low, c = (getattr(bar, name) for name in _PRICE_FIELDS)
    if None not in (o, h, low, c) and not (low <= o <= h and low <= c <= h):
        raise ValueError(f"inconsistent OHLC at {bar.bar_time.isoformat()}: {o} {h} {low} {c}")


@dataclass(frozen=True, slots=True)
class BarRecord:
    """One unadjusted bar for a listing. ``bar_time`` is the bar OPEN time, UTC.

    ``session`` may be ``None``: the DB service derives it from the market
    calendar, which is not provider knowledge.
    """

    instrument: InstrumentRef
    resolution: str
    bar_time: datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None = None
    turnover: Decimal | None = None
    trades_count: int | None = None
    oi: int | None = None
    settlement_price: Decimal | None = None
    session: str | None = None

    def __post_init__(self) -> None:
        _validate_bar(self)


@dataclass(frozen=True, slots=True)
class ContractBarRecord:
    """One bar for a single dated derivative contract (``market.futures_contract_bars``)."""

    contract: InstrumentRef
    resolution: str
    bar_time: datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None = None
    turnover: Decimal | None = None
    trades_count: int | None = None
    oi: int | None = None
    settlement_price: Decimal | None = None
    session: str | None = None

    def __post_init__(self) -> None:
        _validate_bar(self)


@dataclass(frozen=True, slots=True)
class SeriesWindow:
    """Fetch ``instrument`` bars with ``start <= bar_time < end`` (both UTC)."""

    instrument: InstrumentRef
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        require_utc("start", self.start)
        require_utc("end", self.end)
        if self.end <= self.start:
            raise ValueError("SeriesWindow.end must be after start")


@dataclass(frozen=True, slots=True)
class BarRequest:
    """What the engine asks a bar source for. Providers never pick the universe."""

    market: str
    resolution: str
    series: tuple[SeriesWindow, ...]
    params: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if self.resolution not in RESOLUTIONS:
            raise ValueError(f"unknown resolution {self.resolution!r}")


@runtime_checkable
class BarSink(ProviderStorage, Protocol):
    def write_bars(self, rows: Sequence[BarRecord], *, provenance: Provenance) -> WriteResult: ...


@runtime_checkable
class ContractBarSink(ProviderStorage, Protocol):
    def write_contract_bars(
        self, rows: Sequence[ContractBarRecord], *, provenance: Provenance
    ) -> WriteResult: ...


__all__ = [
    "BarRecord",
    "BarRequest",
    "BarSink",
    "ContractBarRecord",
    "ContractBarSink",
    "SeriesWindow",
]
