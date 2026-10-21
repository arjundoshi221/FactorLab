"""``ref.security_sectors`` and ``fundamentals.shares_outstanding`` dataset contracts.

Sector assignments arrive as **complete snapshots** per classification, like
universe membership: the sink diffs them into effective-dated rows in
``ref.security_sector_history`` (a changed sector closes the old row the day before
and opens the new one). Constituent files usually carry bare tickers, so references
may omit the alias.

Shares outstanding are daily vendor observations keyed by listing, day and source;
history accumulates point-in-time from successive snapshots.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID

from factorlab.ingest.datasets.common import (
    InstrumentRef,
    WriteResult,
    require_decimal,
    require_int,
)
from factorlab.ingest.datasets.reference import ReferenceMode
from factorlab.ingest.provider import Provenance, ProviderStorage

CLASSIFICATIONS = frozenset({"gics", "icb", "naics", "trbc"})
_SECTOR_ID = re.compile(r"^[0-9]{2,8}$")
MAX_SECTOR_CHURN = 0.2


@dataclass(frozen=True, slots=True)
class SectorRecord:
    """``instrument`` belongs to ``sector_id`` in the snapshot the capture describes."""

    instrument: InstrumentRef
    sector_id: str
    sector_name: str = ""
    classification: str = "gics"

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(f"unknown classification {self.classification!r}")
        if not _SECTOR_ID.fullmatch(self.sector_id):
            raise ValueError(f"invalid sector_id {self.sector_id!r}")


@dataclass(frozen=True, slots=True)
class SharesOutstandingRecord:
    instrument: InstrumentRef
    as_of: date
    shares_outstanding: int
    market_cap: Decimal | None = None  # vendor-reported, vendor units; cross-check only

    def __post_init__(self) -> None:
        require_int("shares_outstanding", self.shares_outstanding, optional=False)
        if self.shares_outstanding <= 0:
            raise ValueError("shares_outstanding must be positive")
        require_decimal("market_cap", self.market_cap)


@dataclass(frozen=True, slots=True)
class SectorDiff:
    open: tuple[tuple[UUID, str], ...]  # (listing, sector_id) opening on effective_from
    close: tuple[tuple[UUID, str, date], ...]  # (listing, sector_id, its effective_from)
    effective_from: date
    effective_to: date


class SectorSnapshotRejected(ValueError):
    """The snapshot would change assignments implausibly; nothing is written."""


def diff_sectors(
    current: Mapping[UUID, tuple[str, date]],
    snapshot: Mapping[UUID, str],
    *,
    as_of: date,
    max_churn: float = MAX_SECTOR_CHURN,
) -> SectorDiff:
    """``current`` maps listing -> (open sector, since); ``snapshot`` the new assignments.

    Pure, so the in-memory and ClickHouse sinks cannot drift apart.
    """
    if not snapshot:
        raise SectorSnapshotRejected("sector snapshot resolved to no listings")
    changed = [lid for lid, (sector, _) in current.items() if snapshot.get(lid) != sector]
    if current and len(changed) > max_churn * len(current):
        raise SectorSnapshotRejected(
            f"sector snapshot changes {len(changed)} of {len(current)} assignments "
            f"(> {max_churn:.0%}); refusing as implausible"
        )
    opened = [
        (lid, sector)
        for lid, sector in snapshot.items()
        if lid not in current or current[lid][0] != sector
    ]
    return SectorDiff(
        open=tuple(sorted(opened, key=lambda item: str(item[0]))),
        close=tuple(
            sorted(((lid, *current[lid]) for lid in changed), key=lambda item: str(item[0]))
        ),
        effective_from=as_of,
        effective_to=as_of - timedelta(days=1),
    )


@runtime_checkable
class SectorSink(ProviderStorage, Protocol):
    def write_sectors(
        self,
        rows: Sequence[SectorRecord],
        *,
        provenance: Provenance,
        mode: ReferenceMode = "authoritative",
    ) -> WriteResult:
        """Each classification present in ``rows`` is treated as a complete snapshot."""
        ...


@runtime_checkable
class SharesOutstandingSink(ProviderStorage, Protocol):
    def write_shares_outstanding(
        self, rows: Sequence[SharesOutstandingRecord], *, provenance: Provenance
    ) -> WriteResult: ...


__all__ = [
    "CLASSIFICATIONS",
    "MAX_SECTOR_CHURN",
    "SectorDiff",
    "SectorRecord",
    "SectorSink",
    "SectorSnapshotRejected",
    "SharesOutstandingRecord",
    "SharesOutstandingSink",
    "diff_sectors",
]
