"""Read-side DB ports the engine uses to build provider-neutral requests (docs/architecture/07 §8.3, §11.3).

The engine asks *which* canonical listings to fetch and *from when*; the DB
service answers in canonical terms. Provider identifiers appear only as the
``alias_kind`` a source declares in its capabilities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from factorlab.shared.ingest.datasets.common import InstrumentRef
from factorlab.shared.ingest.provider import RawCapture


@runtime_checkable
class ReferenceReader(Protocol):
    def aliases_for(self, listing_ids: Sequence[UUID], *,
                    alias_kind: str) -> Mapping[UUID, InstrumentRef]:
        """Canonical listing -> the provider's :class:`InstrumentRef`; missing ids are unmapped."""
        ...

    def natural_refs(self, listing_ids: Sequence[UUID]) -> Mapping[UUID, InstrumentRef]:
        """Canonical listing -> alias-free ref (exchange, symbol, ISIN) for per-symbol lookups."""
        ...

    def active_listings(self, exchange_code: str) -> list[UUID]:
        """Every active listing on an exchange (e.g. production's ``full_nse_eq`` universe)."""
        ...


@runtime_checkable
class CheckpointStore(Protocol):
    def watermarks(self, listing_ids: Sequence[UUID], *, dataset: str, source: str,
                   resolution: str) -> Mapping[UUID, datetime]:
        """Latest stored ``bar_time`` per listing for this source; absent = never fetched."""
        ...


@dataclass(frozen=True, slots=True)
class ArchivedCapture:
    """A capture read back from ``raw.archive`` with the lineage it was stored under."""

    raw_id: UUID
    source: str
    source_channel: str
    capture: RawCapture


@runtime_checkable
class RawArchiveReader(Protocol):
    def load_raw(self, raw_id: UUID) -> ArchivedCapture:
        """Rebuild the exact capture stored in ``raw.archive`` (for replay)."""
        ...


__all__ = ["ArchivedCapture", "CheckpointStore", "RawArchiveReader", "ReferenceReader"]
