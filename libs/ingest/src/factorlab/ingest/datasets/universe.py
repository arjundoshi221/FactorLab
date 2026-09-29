"""``ref.universe_membership`` dataset contract (docs/architecture/07 §5.4).

A universe source returns the **complete current membership** of each
universe it covers, as of the capture. The sink turns successive snapshots
into effective-dated membership: new members open a row on the snapshot date,
members that disappear are closed the day before. Constituents are usually
bare tickers, so references may omit the alias (country-scoped resolution).

Write modes follow the other reference datasets: only an ``authoritative``
(primary) binding changes membership; other roles resolve and report.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID

from factorlab.ingest.datasets.common import InstrumentRef, WriteResult, require_decimal
from factorlab.ingest.datasets.reference import ReferenceMode
from factorlab.ingest.provider import Provenance, ProviderStorage

_UNIVERSE_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


@dataclass(frozen=True, slots=True)
class ConstituentRecord:
    """One member of ``universe_code`` in the snapshot the capture describes."""

    universe_code: str
    instrument: InstrumentRef
    universe_name: str = ""
    weight: Decimal | None = None

    def __post_init__(self) -> None:
        if not _UNIVERSE_CODE.fullmatch(self.universe_code):
            raise ValueError(f"invalid universe_code {self.universe_code!r}")
        require_decimal("weight", self.weight)
        if self.weight is not None and self.weight < 0:
            raise ValueError("weight must not be negative")


@runtime_checkable
class UniverseSink(ProviderStorage, Protocol):
    def write_constituents(self, rows: Sequence[ConstituentRecord], *, provenance: Provenance,
                           mode: ReferenceMode = "authoritative") -> WriteResult:
        """Each universe present in ``rows`` is treated as a complete snapshot."""
        ...


@runtime_checkable
class UniverseReader(Protocol):
    def universe_members(self, universe_codes: Sequence[str], *,
                         as_of: date | None = None) -> list[UUID]:
        """Listings that belong to any of ``universe_codes`` on ``as_of`` (default: today)."""
        ...


__all__ = ["ConstituentRecord", "UniverseReader", "UniverseSink"]
