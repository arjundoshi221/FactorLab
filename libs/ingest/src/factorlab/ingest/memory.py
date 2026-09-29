"""In-memory sinks for dry runs and tests (docs/architecture/07 §7.3, §11.4).

:class:`InMemorySink` satisfies every dataset sink protocol and enforces the
same lineage guarantee as the ClickHouse sinks: writes need an open run and a
``Provenance`` from that run. Identity is resolved the provider-neutral way
(alias, then natural key, then mint) with deterministic in-memory UUIDs, so
tests can assert that two providers land on the same listing.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from factorlab.ingest.datasets import (
    BarRecord,
    ConstituentRecord,
    ContractBarRecord,
    ContractRecord,
    InstrumentRecord,
    InstrumentRef,
    WriteResult,
)
from factorlab.ingest.datasets.political import (
    CommitteeRecord,
    FilingRef,
    LegislatorRecord,
    MembershipRecord,
    PoliticalFilingRecord,
    PoliticalTradeRecord,
)
from factorlab.ingest.datasets.ports import ArchivedCapture
from factorlab.ingest.datasets.reference import ReferenceMode
from factorlab.ingest.identifiers import legislator_name_key
from factorlab.ingest.provider import NullProviderStorage, Provenance, RawCapture
from factorlab.ingest.universe_snapshot import diff_snapshot

_MEMORY_NAMESPACE = uuid.UUID("0b8f1a52-7d0e-4f0c-9a7e-3f2d9c1e5a40")


@dataclass(frozen=True, slots=True)
class StoredRow:
    record: Any
    provenance: Provenance
    target_id: UUID | None


class InMemorySink(NullProviderStorage):
    def __init__(self) -> None:
        super().__init__()
        self._active_run: UUID | None = None
        self.aliases: dict[tuple[str, str], UUID] = {}
        self.contract_aliases: dict[tuple[str, str], UUID] = {}
        self.listings: dict[UUID, InstrumentRef] = {}
        self.contracts: dict[UUID, tuple[UUID, ContractRecord]] = {}
        self.rows: dict[str, list[StoredRow]] = {}
        self.unresolved: list[tuple[str, str, str]] = []
        self._raw: dict[UUID, ArchivedCapture] = {}
        # universe_code -> listing -> [effective_from, effective_to | None]
        self.memberships: dict[str, dict[UUID, list[Any]]] = {}
        # political: (alias_kind, alias_value) -> entity ids; filings by (chamber, filing_id)
        self.entity_aliases: dict[tuple[str, str], set[UUID]] = {}
        self.committees: set[str] = set()
        self.filings: dict[tuple[str, str], dict[str, Any]] = {}

    # -- ProviderStorage ---------------------------------------------------
    def archive_raw(self, capture: RawCapture, *, source: str, source_channel: str) -> UUID:
        raw_id = super().archive_raw(capture, source=source, source_channel=source_channel)
        self._raw[raw_id] = ArchivedCapture(raw_id, source, source_channel, capture)
        return raw_id

    def start_ingestion_run(self, **kwargs: Any) -> Any:
        handle = super().start_ingestion_run(**kwargs)
        self._active_run = handle.run_id
        return handle

    def finish_ingestion_run(self, handle: Any, **kwargs: Any) -> None:
        super().finish_ingestion_run(handle, **kwargs)
        self._active_run = None

    def load_raw(self, raw_id: UUID) -> ArchivedCapture:
        return self._raw[raw_id]

    # -- lineage and identity ----------------------------------------------
    def _check_lineage(self, provenance: Provenance) -> None:
        if self._active_run is None:
            raise RuntimeError("start an ingestion run before writing")
        if provenance.ingest_run_id != self._active_run:
            raise RuntimeError("provenance belongs to a different ingestion run")

    @staticmethod
    def _in_scope(ref: InstrumentRef, known: InstrumentRef) -> bool:
        if ref.exchange_code:
            return known.exchange_code == ref.exchange_code
        return known.country_code == ref.country_code

    def _natural_match(self, ref: InstrumentRef) -> UUID | None:
        by_isin = [lid for lid, known in self.listings.items()
                   if ref.isin and known.isin == ref.isin and self._in_scope(ref, known)]
        if len(by_isin) == 1:
            return by_isin[0]
        if len(by_isin) > 1:
            return None  # ambiguous: never guess
        by_symbol = [lid for lid, known in self.listings.items()
                     if self._in_scope(ref, known) and known.trading_symbol == ref.trading_symbol]
        if len(by_symbol) != 1:
            return None
        known_isin = self.listings[by_symbol[0]].isin
        if ref.isin and known_isin and known_isin != ref.isin:
            return None  # same ticker, different security (ticker reuse)
        return by_symbol[0]

    def resolve_listing(self, ref: InstrumentRef) -> UUID | None:
        if ref.has_alias and (ref.alias_kind, ref.alias_value) in self.aliases:
            return self.aliases[(ref.alias_kind, ref.alias_value)]
        return self._natural_match(ref)

    def _park(self, source: str, ref: InstrumentRef, reason: str) -> None:
        self.unresolved.append((source, ref.label(), reason))

    def _store(self, table: str, record: Any, provenance: Provenance,
               target: UUID | None) -> None:
        self.rows.setdefault(table, []).append(StoredRow(record, provenance, target))

    # -- sinks -------------------------------------------------------------
    def upsert_instruments(self, rows: Sequence[InstrumentRecord], *, provenance: Provenance,
                           mode: ReferenceMode = "authoritative") -> WriteResult:
        self._check_lineage(provenance)
        written = unresolved = resolved = 0
        for row in rows:
            listing = self.resolve_listing(row.ref)
            if listing is not None:
                resolved += 1
            elif mode == "authoritative":
                key = f"{row.ref.exchange_code}:{row.ref.isin or row.ref.trading_symbol}"
                listing = uuid.uuid5(_MEMORY_NAMESPACE, f"listing:{key}")
            if listing is None:
                self._park(provenance.source, row.ref, f"no listing ({mode} mode cannot mint)")
                unresolved += 1
                continue
            if mode == "resolve_only":
                continue
            if mode == "authoritative":
                self.listings[listing] = row.ref
            self.aliases[(row.ref.alias_kind, row.ref.alias_value)] = listing
            self._store("ref.listings", row, provenance, listing)
            written += 1
        return WriteResult(written, unresolved, resolved)

    def upsert_contracts(self, rows: Sequence[ContractRecord], *, provenance: Provenance,
                         mode: ReferenceMode = "authoritative") -> WriteResult:
        self._check_lineage(provenance)
        written = unresolved = resolved = 0
        for row in rows:
            underlying = self.resolve_listing(row.underlying)
            if underlying is None:
                self._park(provenance.source, row.underlying, "underlying listing unresolved")
                unresolved += 1
                continue
            contract = self.contract_aliases.get((row.ref.alias_kind, row.ref.alias_value))
            if contract is None:
                contract = next((cid for cid, (lid, known) in self.contracts.items()
                                 if lid == underlying and known.expiry == row.expiry
                                 and known.right == row.right and known.strike == row.strike),
                                None)
            if contract is not None:
                resolved += 1
            elif mode == "authoritative":
                contract = uuid.uuid5(_MEMORY_NAMESPACE, f"contract:{underlying}:{row.expiry}:"
                                                        f"{row.right}:{row.strike}")
            if contract is None:
                self._park(provenance.source, row.ref, f"no contract ({mode} mode cannot mint)")
                unresolved += 1
                continue
            if mode == "resolve_only":
                continue
            if mode == "authoritative":
                self.contracts[contract] = (underlying, row)
            self.contract_aliases[(row.ref.alias_kind, row.ref.alias_value)] = contract
            self._store("ref.contracts", row, provenance, contract)
            written += 1
        return WriteResult(written, unresolved, resolved)

    def write_constituents(self, rows: Sequence[ConstituentRecord], *, provenance: Provenance,
                           mode: ReferenceMode = "authoritative") -> WriteResult:
        self._check_lineage(provenance)
        written = unresolved = resolved = 0
        as_of = provenance.as_of_time.astimezone(UTC).date()
        by_universe: dict[str, set[UUID]] = {}
        for row in rows:
            listing = self.resolve_listing(row.instrument)
            by_universe.setdefault(row.universe_code, set())
            if listing is None:
                self._park(provenance.source, row.instrument, f"{row.universe_code}: unresolved")
                unresolved += 1
                continue
            resolved += 1
            by_universe[row.universe_code].add(listing)
        if mode != "authoritative":
            return WriteResult(0, unresolved, resolved)
        for code, snapshot in by_universe.items():
            members = self.memberships.setdefault(code, {})
            current = {lid: span[0] for lid, span in members.items() if span[1] is None}
            diff = diff_snapshot(code, current, snapshot, as_of=as_of)
            for listing, _ in diff.close:
                members[listing][1] = diff.effective_to
            for listing in diff.add:
                members[listing] = [diff.effective_from, None]
            written += len(diff.add) + len(diff.close)
        return WriteResult(written, unresolved, resolved)

    # -- political -------------------------------------------------------------
    def write_legislators(self, rows: Sequence[Any], *, provenance: Provenance,
                          mode: ReferenceMode = "authoritative") -> WriteResult:
        self._check_lineage(provenance)
        if mode != "authoritative":
            return WriteResult(0, 0, len(rows))
        written = parked = 0
        for row in rows:
            if isinstance(row, LegislatorRecord):
                entity = uuid.uuid5(_MEMORY_NAMESPACE, f"bioguide:{row.entity.alias_value}")
                self.entity_aliases.setdefault(("bioguide", row.entity.alias_value),
                                               set()).add(entity)
                key = legislator_name_key(row.first_name, row.last_name)
                if key:
                    self.entity_aliases.setdefault(("legislator_name", key), set()).add(entity)
            elif isinstance(row, CommitteeRecord):
                self.committees.add(row.committee_code)
            elif isinstance(row, MembershipRecord):
                if (row.committee_code not in self.committees
                        or ("bioguide", row.legislator.alias_value) not in self.entity_aliases):
                    parked += 1
                    continue
            self._store("ref.legislators", row, provenance, None)
            written += 1
        return WriteResult(written, parked, written)

    def write_political_filings(self, rows: Sequence[PoliticalFilingRecord], *,
                                provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)
        unresolved = 0
        for row in rows:
            targets = self.entity_aliases.get(("legislator_name", row.filer.alias_value),
                                              set()) if row.filer else set()
            entity = next(iter(targets)) if len(targets) == 1 else None
            unresolved += int(row.filer is not None and entity is None)
            prior = self.filings.get((row.chamber, row.filing_id), {})
            self.filings[(row.chamber, row.filing_id)] = {
                "record": row, "entity": entity, "trade_count": prior.get("trade_count", 0)}
            self._store("alt.political_filings", row, provenance, entity)
        return WriteResult(len(rows), unresolved, len(rows) - unresolved)

    def write_political_trades(self, rows: Sequence[PoliticalTradeRecord], *,
                               provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)
        written = orphans = unmatched = 0
        for row in rows:
            filing = self.filings.get((row.chamber, row.filing_id))
            if filing is None:
                orphans += 1
                continue
            listing = self.resolve_listing(row.instrument) if row.instrument else None
            unmatched += int(row.instrument is not None and listing is None)
            filing["trade_count"] = filing.get("trade_count", 0) + 1
            self._store("alt.political_trades", row, provenance, listing)
            written += 1
        return WriteResult(written, orphans, written - unmatched)

    def recent_filings(self, *, chamber: str, limit: int,
                       country_code: str = "US") -> list[FilingRef]:
        found = [f["record"] for (c, _), f in self.filings.items()
                 if c == chamber and f["record"].country_code == country_code]
        found.sort(key=lambda r: (r.filing_date, r.filing_id), reverse=True)
        return [FilingRef(r.filing_id, r.filing_year, r.filing_url) for r in found[:limit]]

    def write_fundamentals(self, rows: Sequence[Any], *, provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)
        written = unresolved = 0
        for row in rows:
            targets = self.entity_aliases.get((row.issuer.alias_kind, row.issuer.alias_value), set())
            target = next(iter(targets)) if len(targets) == 1 else None
            if target is None and row.issuer_hint is not None:
                target = self.resolve_listing(row.issuer_hint)  # the listing stands in for its issuer
                if target is not None:
                    self.entity_aliases.setdefault(
                        (row.issuer.alias_kind, row.issuer.alias_value), set()).add(target)
            if target is None:
                unresolved += 1
                continue
            self._store(f"fundamentals.{type(row).__name__}", row, provenance, target)
            written += 1
        return WriteResult(written, unresolved, written)

    def write_snapshot(self, rows: Sequence[Any], *, provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)
        for row in rows:
            self._store(f"broker.{type(row).__name__}", row, provenance, None)
        return WriteResult(len(rows), 0, len(rows))

    def universe_members(self, universe_codes: Sequence[str], *,
                         as_of: date | None = None) -> list[UUID]:
        day = as_of or datetime.now(UTC).date()
        found = {lid for code in universe_codes
                 for lid, (start, end) in self.memberships.get(code, {}).items()
                 if start <= day and (end is None or end >= day)}
        return sorted(found, key=str)

    def _write_facts(self, table: str, rows: Sequence[Any], refs: Sequence[InstrumentRef],
                     provenance: Provenance, resolve: Any) -> WriteResult:
        self._check_lineage(provenance)
        written = unresolved = 0
        for row, ref in zip(rows, refs, strict=True):
            target = resolve(ref)
            if target is None:
                self._park(provenance.source, ref, f"{table}: instrument unresolved")
                unresolved += 1
                continue
            self._store(table, row, provenance, target)
            written += 1
        return WriteResult(written, unresolved, written)

    def write_bars(self, rows: Sequence[BarRecord], *, provenance: Provenance) -> WriteResult:
        return self._write_facts("market.bars", rows, [r.instrument for r in rows], provenance,
                                 self.resolve_listing)

    def write_contract_bars(self, rows: Sequence[ContractBarRecord], *,
                            provenance: Provenance) -> WriteResult:
        return self._write_facts(
            "market.futures_contract_bars", rows, [r.contract for r in rows], provenance,
            lambda ref: self.contract_aliases.get((ref.alias_kind, ref.alias_value)),
        )

    # -- read ports ----------------------------------------------------------
    def aliases_for(self, listing_ids: Sequence[UUID], *,
                    alias_kind: str) -> Mapping[UUID, InstrumentRef]:
        wanted = set(listing_ids)
        found: dict[UUID, InstrumentRef] = {}
        for (kind, value), target in self.aliases.items():
            if kind == alias_kind and target in wanted and target in self.listings:
                known = self.listings[target]
                found[target] = InstrumentRef(
                    alias_kind=kind, alias_value=value, exchange_code=known.exchange_code,
                    trading_symbol=known.trading_symbol, country_code=known.country_code,
                    isin=known.isin,
                )
        return found

    def active_listings(self, exchange_code: str) -> list[UUID]:
        return sorted((lid for lid, ref in self.listings.items()
                       if ref.exchange_code == exchange_code), key=str)

    def natural_refs(self, listing_ids: Sequence[UUID]) -> Mapping[UUID, InstrumentRef]:
        return {lid: InstrumentRef("", "", known.exchange_code, known.trading_symbol,
                                   known.country_code, isin=known.isin)
                for lid, known in self.listings.items() if lid in set(listing_ids)}

    def watermarks(self, listing_ids: Sequence[UUID], *, dataset: str, source: str,
                   resolution: str) -> Mapping[UUID, datetime]:
        wanted = set(listing_ids)
        marks: dict[UUID, datetime] = {}
        for stored in self.rows.get(dataset, []):
            record = stored.record
            if (stored.target_id in wanted and stored.provenance.source == source
                    and record.resolution == resolution):
                current = marks.get(stored.target_id)
                if current is None or record.bar_time > current:
                    marks[stored.target_id] = record.bar_time
        return marks

    def count(self, table: str, *, source: str | None = None) -> int:
        return sum(1 for row in self.rows.get(table, [])
                   if source is None or row.provenance.source == source)


__all__ = ["InMemorySink", "StoredRow"]
