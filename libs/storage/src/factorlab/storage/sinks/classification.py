"""ClickHouse sinks for ``ref.security_sectors`` and ``fundamentals.shares_outstanding``.

Sector snapshots become effective-dated rows in ``ref.security_sector_history``
through the shared pure diff (``factorlab.ingest.datasets.classification``), so this
sink and ``InMemorySink`` cannot drift. Sector names seen in a snapshot are added to
``ref.sectors`` when missing; existing names are never rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from factorlab.ingest.datasets import InstrumentRef, WriteResult
from factorlab.ingest.datasets.classification import (
    SectorRecord,
    SharesOutstandingRecord,
    diff_sectors,
)
from factorlab.ingest.datasets.reference import ReferenceMode
from factorlab.ingest.provider import Provenance
from factorlab.storage.clickhouse import _decoded_text, _version
from factorlab.storage.sinks.identity import IdentityResolver


class ClassificationSinkMixin:
    """Mixed into ``ClickHouseSinks``; relies on ``client``, ``_insert``, ``_park``."""

    client: Any

    # -- ref.security_sectors ---------------------------------------------------
    def _open_sectors(self, classification: str) -> dict[UUID, tuple[str, date]]:
        rows = self.client.query(
            "SELECT listing_id, sector_id, effective_from FROM ref.security_sector_history FINAL "
            "WHERE classification = {classification:String} AND effective_to IS NULL",
            parameters={"classification": classification},
        ).result_rows
        return {row[0]: (_decoded_text(row[1]), row[2]) for row in rows}

    def _ensure_sectors(self, classification: str, names: dict[str, str]) -> None:
        known = {
            _decoded_text(row[0])
            for row in self.client.query(
                "SELECT sector_id FROM ref.sectors FINAL "
                "WHERE classification = {classification:String}",
                parameters={"classification": classification},
            ).result_rows
        }
        now = datetime.now(UTC)
        self._insert(  # type: ignore[attr-defined]
            "ref.sectors",
            [
                {
                    "sector_id": sector_id,
                    "level": len(sector_id),
                    "parent_id": sector_id[:-2] if len(sector_id) > 2 else None,
                    "name": name or sector_id,
                    "classification": classification,
                    "active": True,
                    "version": _version(now),
                    "ingested_at": now,
                }
                for sector_id, name in sorted(names.items())
                if sector_id not in known
            ],
        )

    def write_sectors(
        self,
        rows: Sequence[SectorRecord],
        *,
        provenance: Provenance,
        mode: ReferenceMode = "authoritative",
    ) -> WriteResult:
        """Each classification in ``rows`` is a complete snapshot as of ``provenance.as_of_time``."""
        self._check_lineage(provenance)  # type: ignore[attr-defined]
        identities = IdentityResolver(self.client).listings(row.instrument for row in rows)
        snapshots: dict[str, dict[UUID, str]] = {}
        names: dict[str, dict[str, str]] = {}
        unresolved: list[InstrumentRef] = []
        for row in rows:
            snapshot = snapshots.setdefault(row.classification, {})
            names.setdefault(row.classification, {}).setdefault(row.sector_id, row.sector_name)
            identity = identities.get(row.instrument)
            if identity is None:
                unresolved.append(row.instrument)
                continue
            snapshot[identity.listing_id] = row.sector_id
        if unresolved:
            self._park(provenance, unresolved, "ref.security_sectors: listing unresolved")  # type: ignore[attr-defined]
        resolved = sum(len(snapshot) for snapshot in snapshots.values())
        if mode != "authoritative":
            return WriteResult(0, len(unresolved), resolved)
        as_of = provenance.as_of_time.astimezone(UTC).date()
        now = datetime.now(UTC)
        written = 0
        for classification, snapshot in snapshots.items():
            # Unlike membership, unresolved tickers do not block the snapshot: they are
            # parked, and listings absent from it (resolved or not) close.
            current = self._open_sectors(classification)
            diff = diff_sectors(current, snapshot, as_of=as_of)
            self._ensure_sectors(classification, names[classification])
            base = {
                "classification": classification,
                "source": provenance.source,
                "raw_id": provenance.raw_id,
                "version": _version(now),
                "ingested_at": now,
            }
            records = [
                {
                    **base,
                    "listing_id": listing,
                    "sector_id": sector,
                    "effective_from": since,
                    "effective_to": diff.effective_to,
                }
                for listing, sector, since in diff.close
            ] + [
                {
                    **base,
                    "listing_id": listing,
                    "sector_id": sector,
                    "effective_from": diff.effective_from,
                    "effective_to": None,
                }
                for listing, sector in diff.open
            ]
            self._insert("ref.security_sector_history", records)  # type: ignore[attr-defined]
            written += len(records)
        return WriteResult(written, len(unresolved), resolved)

    # -- fundamentals.shares_outstanding ------------------------------------------
    def write_shares_outstanding(
        self, rows: Sequence[SharesOutstandingRecord], *, provenance: Provenance
    ) -> WriteResult:
        self._check_lineage(provenance)  # type: ignore[attr-defined]
        identities = IdentityResolver(self.client).listings(row.instrument for row in rows)
        now = datetime.now(UTC)
        records: list[dict[str, Any]] = []
        unresolved: list[InstrumentRef] = []
        for row in rows:
            identity = identities.get(row.instrument)
            if identity is None:
                unresolved.append(row.instrument)
                continue
            records.append(
                {
                    "listing_id": identity.listing_id,
                    "as_of_date": row.as_of,
                    "shares_outstanding": row.shares_outstanding,
                    "market_cap": float(row.market_cap) if row.market_cap is not None else None,
                    "source": provenance.source,
                    "source_channel": provenance.source_channel,
                    "raw_id": provenance.raw_id,
                    "ingest_run_id": provenance.ingest_run_id,
                    "as_of_time": provenance.as_of_time,
                    "ingested_at": provenance.ingested_at,
                    "version": _version(now),
                }
            )
        self._insert("fundamentals.shares_outstanding", records)  # type: ignore[attr-defined]
        if unresolved:
            self._park(provenance, unresolved, "fundamentals.shares_outstanding: unresolved")  # type: ignore[attr-defined]
        return WriteResult(len(records), len(unresolved), len(records))


__all__ = ["ClassificationSinkMixin"]
