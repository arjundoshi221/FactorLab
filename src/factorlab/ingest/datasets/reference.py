"""``ref.listings`` and ``ref.contracts`` dataset contracts (docs/architecture/07 §5.4).

Reference sinks take a ``mode`` derived from the binding's role (07 §8.1):

* ``authoritative`` (primary): resolve, mint when new, write attributes and alias
* ``alias_only`` (secondary): attach the provider's alias to an identity that
  already exists; never mint, never overwrite attributes
* ``resolve_only`` (shadow): resolve and report; write nothing to ``ref.*``

Anything unresolvable is parked in ``meta.unresolved_entities`` in every mode.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

from factorlab.ingest.datasets.common import InstrumentRef, WriteResult, require_decimal
from factorlab.ingest.provider import Provenance, ProviderStorage

ReferenceMode = Literal["authoritative", "alias_only", "resolve_only"]
REFERENCE_MODES: frozenset[str] = frozenset({"authoritative", "alias_only", "resolve_only"})

PRODUCT_TYPES = frozenset({
    "common", "preferred", "adr", "etf", "etn", "reit", "warrant", "index",
    "index_future", "single_stock_future", "vol_future", "vol_etp", "option", "other",
})
CONTRACT_PRODUCT_TYPES = frozenset({"single_stock_future", "index_future", "vol_future", "option"})


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    """One listing as a provider describes it."""

    ref: InstrumentRef
    name: str
    product_type: str
    currency: str
    lot_size: int = 1
    tick_size: Decimal | None = None
    active: bool = True
    first_traded: date | None = None
    last_traded: date | None = None
    sector_raw: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if not self.ref.has_alias:
            raise ValueError("InstrumentRecord.ref must carry the provider alias")
        if self.product_type not in PRODUCT_TYPES:
            raise ValueError(f"unknown product_type {self.product_type!r}")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"currency must be ISO-4217, got {self.currency!r}")
        if self.lot_size < 1:
            raise ValueError("lot_size must be positive")
        require_decimal("tick_size", self.tick_size)


@dataclass(frozen=True, slots=True)
class ContractRecord:
    """One dated derivative contract and the listing it is written on."""

    ref: InstrumentRef
    underlying: InstrumentRef
    product_type: str
    expiry: date
    right: str | None = None
    strike: Decimal | None = None
    lot_size: int = 1
    tick_size: Decimal | None = None
    multiplier: int = 1
    weekly: bool = False
    active: bool = True
    attributes: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if not self.ref.has_alias:
            raise ValueError("ContractRecord.ref must carry the provider alias")
        if self.product_type not in CONTRACT_PRODUCT_TYPES:
            raise ValueError(f"unknown contract product_type {self.product_type!r}")
        if not isinstance(self.expiry, date):
            raise TypeError("expiry must be a date")
        if self.right not in (None, "C", "P"):
            raise ValueError(f"right must be 'C', 'P' or None, got {self.right!r}")
        if (self.right is None) != (self.strike is None):
            raise ValueError("options need both right and strike; futures need neither")
        require_decimal("strike", self.strike)
        require_decimal("tick_size", self.tick_size)
        if self.lot_size < 1 or self.multiplier < 1:
            raise ValueError("lot_size and multiplier must be positive")


@dataclass(frozen=True, slots=True)
class ReferenceRequest:
    """Reference datasets pull the provider's master; ``params`` narrows it.

    ``instruments`` carries canonical listings (natural keys, no alias) for
    providers without a master file that must look instruments up one by one.
    """

    market: str
    params: Mapping[str, Any] = field(default_factory=dict, hash=False)
    instruments: tuple[InstrumentRef, ...] = ()


@runtime_checkable
class InstrumentSink(ProviderStorage, Protocol):
    def upsert_instruments(self, rows: Sequence[InstrumentRecord], *, provenance: Provenance,
                           mode: ReferenceMode = "authoritative") -> WriteResult: ...


@runtime_checkable
class ContractSink(ProviderStorage, Protocol):
    def upsert_contracts(self, rows: Sequence[ContractRecord], *, provenance: Provenance,
                         mode: ReferenceMode = "authoritative") -> WriteResult: ...


__all__ = [
    "CONTRACT_PRODUCT_TYPES",
    "PRODUCT_TYPES",
    "REFERENCE_MODES",
    "ContractRecord",
    "ContractSink",
    "InstrumentRecord",
    "InstrumentSink",
    "ReferenceMode",
    "ReferenceRequest",
]
