"""ClickHouse sinks for the political datasets (docs/architecture/07 §5.5, P5).

Column mappings follow ``storage.v2_political.V2PoliticalClickHouseStorage``
(the production writer) so the tables read the same; what moves out of the
DB service is provider knowledge (vendor JSON shapes, title parsing, the
in-memory name resolver). Two behaviour fixes over the legacy writer:

* re-syncing a filing index keeps each filing's ``trade_count`` instead of
  resetting it to 0;
* a filer name shared by two legislators is left unresolved, not guessed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from factorlab.shared.ingest.datasets import EntityRef, InstrumentRef, WriteResult
from factorlab.shared.ingest.datasets.political import (
    CommitteeRecord,
    FilingRef,
    LegislatorRecord,
    MembershipRecord,
    PoliticalFilingRecord,
    PoliticalTradeRecord,
    congress_number,
)
from factorlab.shared.ingest.datasets.reference import ReferenceMode
from factorlab.shared.ingest.identifiers import legislator_name_key
from factorlab.shared.ingest.provider import Provenance
from factorlab.storage.canonical_ids import committee_id, legislator_id, political_trade_id
from factorlab.storage.clickhouse import _decoded_text, _version

FILING_COLUMNS = (
    "filing_id", "country_code", "chamber", "filing_type", "filing_year", "filing_date",
    "filer_name_raw", "bioguide_id", "legislator_entity_id", "bioguide_confidence",
    "filing_url", "amends_filing_id", "original_filing_id", "amendment_seq", "is_amended",
    "is_amendment", "trade_count", "source", "source_channel", "raw_id", "ingest_run_id",
    "as_of_time", "ingested_at", "version",
)


class PoliticalSinkMixin:
    """Mixed into ``ClickHouseSinks``; relies on ``client``, ``_insert``, ``_check_lineage``."""

    client: Any

    # -- helpers -----------------------------------------------------------------
    def _park_entities(self, provenance: Provenance, refs: Iterable[EntityRef],
                       reason: str) -> None:
        now = datetime.now(UTC)
        self._insert("meta.unresolved_entities", [{  # type: ignore[attr-defined]
            "first_seen": now, "last_seen": now, "source": provenance.source,
            "alias_kind": ref.alias_kind, "alias_value": ref.alias_value,
            "scope_country": "US", "scope_exchange": None,
            "context_json": json.dumps({"raw_id": str(provenance.raw_id) if provenance.raw_id
                                        else None, "name": ref.name}, sort_keys=True),
            "occurrence_count": 1, "retry_count": 0, "last_retry_at": now, "resolved_at": None,
            "resolved_target_kind": None, "resolved_target_id": None, "resolved_by": None,
            "resolution_note": reason, "version": _version(now), "ingested_at": now,
        } for ref in dict.fromkeys(refs)])

    def _existing_entities(self, ids: Iterable[UUID]) -> set[UUID]:
        wanted = sorted(set(ids), key=str)
        if not wanted:
            return set()
        rows = self.client.query(
            "SELECT entity_id FROM ref.entities FINAL WHERE entity_id IN {ids:Array(UUID)}",
            parameters={"ids": wanted},
        ).result_rows
        return {row[0] for row in rows}

    def _entity_aliases(self, kind: str, values: Iterable[str]) -> dict[str, set[UUID]]:
        wanted = sorted(set(values))
        if not wanted:
            return {}
        rows = self.client.query(
            "SELECT alias_value, target_id FROM ref.identifier_aliases FINAL "
            "WHERE alias_kind = {kind:String} AND target_kind = 'entity' "
            "AND valid_to IS NULL AND alias_value IN {values:Array(String)}",
            parameters={"kind": kind, "values": wanted},
        ).result_rows
        found: dict[str, set[UUID]] = {}
        for value, target in rows:
            found.setdefault(_decoded_text(value), set()).add(target)
        return found

    def _bioguides(self, entity_ids: Iterable[UUID]) -> dict[UUID, str]:
        wanted = sorted(set(entity_ids), key=str)
        if not wanted:
            return {}
        rows = self.client.query(
            "SELECT target_id, alias_value FROM ref.identifier_aliases FINAL "
            "WHERE alias_kind = 'bioguide' AND target_kind = 'entity' AND valid_to IS NULL "
            "AND target_id IN {ids:Array(UUID)}",
            parameters={"ids": wanted},
        ).result_rows
        return {row[0]: _decoded_text(row[1]) for row in rows}

    # -- ref.legislators -----------------------------------------------------------
    def write_legislators(self, rows: Sequence[Any], *, provenance: Provenance,
                          mode: ReferenceMode = "authoritative") -> WriteResult:
        self._check_lineage(provenance)  # type: ignore[attr-defined]
        legislators = [r for r in rows if isinstance(r, LegislatorRecord)]
        committees = [r for r in rows if isinstance(r, CommitteeRecord)]
        memberships = [r for r in rows if isinstance(r, MembershipRecord)]
        if mode != "authoritative":
            return WriteResult(0, 0, len(rows))
        written = self._write_legislators(legislators, provenance)
        written += self._write_committees(committees, provenance)
        members, parked = self._write_memberships(memberships, provenance)
        return WriteResult(written + members, parked, written + members)

    def _write_legislators(self, rows: list[LegislatorRecord], provenance: Provenance) -> int:
        now = datetime.now(UTC)
        today = provenance.as_of_time.astimezone(UTC).date()
        entities, aliases, terms = [], [], []
        for row in rows:
            bioguide = row.entity.alias_value
            entity_id = legislator_id(bioguide)
            latest = max(row.terms, key=lambda term: term.start)
            version = _version(now)
            entities.append({
                "entity_id": entity_id, "entity_type": "person_legislator",
                "legal_name": row.entity.name or bioguide, "lei": None,
                "country_of_domicile": "US", "country_of_incorp": "US", "active": True,
                "first_seen": latest.start, "last_seen": latest.end,
                "version": version, "ingested_at": now,
            })
            keys = [("bioguide", bioguide, "exact")]
            name_key = legislator_name_key(row.first_name, row.last_name)
            if name_key:
                keys.append(("legislator_name", name_key, "high"))
            aliases += [{
                "alias_kind": kind, "alias_value": value, "scope_country": "US",
                "scope_exchange": None, "target_kind": "entity", "target_id": entity_id,
                "source": provenance.source, "valid_from": latest.start, "valid_to": None,
                "confidence": confidence, "notes": "", "version": version, "ingested_at": now,
            } for kind, value, confidence in keys]
            terms += [{
                "legislator_entity_id": entity_id, "bioguide_id": bioguide,
                "congress_number": congress_number(term.start), "chamber": term.chamber,
                "state": term.state, "district": term.district, "party": term.party,
                "seat_class": term.seat_class, "term_start": term.start, "term_end": term.end,
                "in_office": term.end >= today, "source": provenance.source,
                "raw_id": provenance.raw_id, "as_of_time": provenance.as_of_time,
                "version": version, "ingested_at": now,
            } for term in row.terms]
        self._insert("ref.entities", entities)  # type: ignore[attr-defined]
        self._insert("ref.identifier_aliases", aliases)  # type: ignore[attr-defined]
        self._insert("ref.legislator_terms", terms)  # type: ignore[attr-defined]
        return len(entities)

    def _write_committees(self, rows: list[CommitteeRecord], provenance: Provenance) -> int:
        now = datetime.now(UTC)
        today = provenance.as_of_time.astimezone(UTC).date()
        entities, committees = [], []
        for row in rows:
            entity_id = committee_id(row.committee_code)
            version = _version(now)
            entities.append({
                "entity_id": entity_id, "entity_type": "committee", "legal_name": row.name,
                "lei": None, "country_of_domicile": "US", "country_of_incorp": "US",
                "active": True, "first_seen": today, "last_seen": today,
                "version": version, "ingested_at": now,
            })
            committees.append({
                "committee_id": row.committee_code, "committee_entity_id": entity_id,
                "country_code": "US", "congress_number": row.congress,
                "parent_committee_id": row.parent_code, "chamber": row.chamber,
                "name": row.name, "jurisdiction": row.jurisdiction, "url": row.url,
                "is_subcommittee": row.is_subcommittee, "effective_from": today,
                "effective_to": None, "source": provenance.source, "raw_id": provenance.raw_id,
                "ingest_run_id": provenance.ingest_run_id, "as_of_time": provenance.as_of_time,
                "ingested_at": now, "version": version,
            })
        self._insert("ref.entities", entities)  # type: ignore[attr-defined]
        self._insert("alt.political_committees", committees)  # type: ignore[attr-defined]
        return len(committees)

    def _write_memberships(self, rows: list[MembershipRecord],
                           provenance: Provenance) -> tuple[int, int]:
        now = datetime.now(UTC)
        today = provenance.as_of_time.astimezone(UTC).date()
        known = self._existing_entities(
            [legislator_id(r.legislator.alias_value) for r in rows]
            + [committee_id(r.committee_code) for r in rows])
        records, missing = [], []
        for row in rows:
            member = legislator_id(row.legislator.alias_value)
            committee = committee_id(row.committee_code)
            if member not in known or committee not in known:
                missing.append(row.legislator if member not in known else EntityRef(
                    "committee_code", row.committee_code, "committee"))
                continue
            records.append({
                "country_code": "US", "congress_number": row.congress,
                "committee_id": row.committee_code, "committee_entity_id": committee,
                "bioguide_id": row.legislator.alias_value, "legislator_entity_id": member,
                "role": row.role, "majority_status": row.party_side, "rank": row.rank,
                "effective_from": today, "effective_to": None, "source": provenance.source,
                "raw_id": provenance.raw_id, "ingest_run_id": provenance.ingest_run_id,
                "as_of_time": provenance.as_of_time, "ingested_at": now,
                "version": _version(now),
            })
        self._insert("alt.political_committee_memberships", records)  # type: ignore[attr-defined]
        if missing:
            self._park_entities(provenance, missing, "membership references an unknown entity")
        return len(records), len(missing)

    # -- alt.political_filings -----------------------------------------------------
    def _existing_filings(self, country: str, chamber: str,
                          filing_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        wanted = sorted(set(filing_ids))
        if not wanted:
            return {}
        rows = self.client.query(
            f"SELECT {', '.join(FILING_COLUMNS)} FROM alt.political_filings FINAL "
            "WHERE country_code = {country:String} AND chamber = {chamber:String} "
            "AND filing_id IN {ids:Array(String)}",
            parameters={"country": country, "chamber": chamber, "ids": wanted},
        ).result_rows
        return {_decoded_text(row[0]): dict(zip(FILING_COLUMNS, row, strict=True))
                for row in rows}

    def write_political_filings(self, rows: Sequence[PoliticalFilingRecord], *,
                                provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        names = self._entity_aliases("legislator_name",
                                     [r.filer.alias_value for r in rows if r.filer])
        resolved_entities = {value: next(iter(targets)) for value, targets in names.items()
                             if len(targets) == 1}  # a shared name is never guessed
        bioguides = self._bioguides(resolved_entities.values())
        previous: dict[tuple[str, str, str], dict[str, Any]] = {}
        for country, chamber in {(r.country_code, r.chamber) for r in rows}:
            for filing_id, record in self._existing_filings(
                    country, chamber, [r.filing_id for r in rows
                                       if (r.country_code, r.chamber) == (country, chamber)]
            ).items():
                previous[(country, chamber, filing_id)] = record
        records, unresolved = [], []
        for row in rows:
            entity = resolved_entities.get(row.filer.alias_value) if row.filer else None
            if row.filer and entity is None:
                unresolved.append(row.filer)
            prior = previous.get((row.country_code, row.chamber, row.filing_id), {})
            records.append({
                "filing_id": row.filing_id, "country_code": row.country_code,
                "chamber": row.chamber, "filing_type": row.filing_type,
                "filing_year": row.filing_year, "filing_date": row.filing_date,
                "filer_name_raw": row.filer_name_raw,
                "bioguide_id": bioguides.get(entity) if entity else None,
                "legislator_entity_id": entity,
                "bioguide_confidence": "exact" if entity else "unresolved",
                "filing_url": row.filing_url, "amends_filing_id": None,
                "original_filing_id": None, "amendment_seq": 0, "is_amended": False,
                "is_amendment": False, "trade_count": int(prior.get("trade_count") or 0),
                "source": provenance.source, "source_channel": provenance.source_channel,
                "raw_id": provenance.raw_id, "ingest_run_id": provenance.ingest_run_id,
                "as_of_time": provenance.as_of_time, "ingested_at": now,
                "version": _version(now),
            })
        self._insert("alt.political_filings", records)  # type: ignore[attr-defined]
        if unresolved:
            self._park_entities(provenance, unresolved, "filer name matches no single legislator")
        return WriteResult(len(records), len(unresolved), len(records) - len(unresolved))

    # -- alt.political_trades ------------------------------------------------------
    def _listing_on(self, ref: InstrumentRef, day: date) -> tuple[Any, ...] | None:
        rows = self.client.query(
            "SELECT DISTINCT l.listing_id, l.security_id, s.entity_id, s.security_type "
            "FROM ref.listings AS l FINAL "
            "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
            "WHERE l.trading_symbol = {ticker:String} AND l.country_code = {country:String} "
            "AND (l.first_traded IS NULL OR l.first_traded <= {day:Date}) "
            "AND (l.last_traded IS NULL OR l.last_traded >= {day:Date})",
            parameters={"ticker": ref.trading_symbol, "country": ref.country_code, "day": day},
        ).result_rows
        return rows[0] if len(rows) == 1 else None

    def write_political_trades(self, rows: Sequence[PoliticalTradeRecord], *,
                               provenance: Provenance) -> WriteResult:
        self._check_lineage(provenance)  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        filings: dict[tuple[str, str, str], dict[str, Any]] = {}
        for country, chamber in {(r.country_code, r.chamber) for r in rows}:
            for filing_id, record in self._existing_filings(
                    country, chamber, [r.filing_id for r in rows
                                       if (r.country_code, r.chamber) == (country, chamber)]
            ).items():
                filings[(country, chamber, filing_id)] = record
        records, orphans, unmatched = [], [], []
        listings: dict[tuple[InstrumentRef, date], tuple[Any, ...] | None] = {}
        for row in rows:
            filing = filings.get((row.country_code, row.chamber, row.filing_id))
            if filing is None:
                orphans.append(EntityRef("filing_id", row.filing_id, "filing"))
                continue
            listing = None
            if row.instrument is not None:
                key = (row.instrument, row.transaction_date)
                if key not in listings:
                    listings[key] = self._listing_on(row.instrument, row.transaction_date)
                listing = listings[key]
                if listing is None:
                    unmatched.append(row.instrument)
            entity = filing["legislator_entity_id"]
            records.append({
                "political_trade_id": political_trade_id(row.native_key),
                "country_code": row.country_code, "chamber": row.chamber,
                "filing_id": row.filing_id, "filing_url": filing["filing_url"],
                "filing_date": filing["filing_date"], "transaction_date": row.transaction_date,
                "notification_date": row.notification_date,
                "legislator_name_raw": filing["filer_name_raw"],
                "bioguide_id": filing["bioguide_id"] if entity else None,
                "legislator_entity_id": entity,
                "bioguide_confidence": "exact" if entity else "unresolved",
                "ticker_raw": row.ticker_raw, "asset_name_raw": row.asset_name_raw,
                "filing_asset_type_code": row.asset_type_code,
                "security_type": _decoded_text(listing[3]) if listing else "other",
                "listing_id": listing[0] if listing else None, "contract_id": None,
                "security_id": listing[1] if listing else None,
                "entity_id": listing[2] if listing else None,
                "resolution_confidence": "exact" if listing else "unresolved",
                "transaction_type": row.transaction_type, "owner_code": row.owner_code,
                "filer_type": row.filer_type, "amount_bucket_id": row.amount_str,
                "amount_min": row.amount_min, "amount_max": row.amount_max,
                "amount_currency": "USD", "amount_str_raw": row.amount_str,
                "source": provenance.source, "source_channel": provenance.source_channel,
                "parser_version": row.parser_version, "raw_id": provenance.raw_id,
                "ingest_run_id": provenance.ingest_run_id, "as_of_time": provenance.as_of_time,
                "ingested_at": now, "version": _version(now),
            })
        self._insert("alt.political_trades", records)  # type: ignore[attr-defined]
        counts: dict[tuple[str, str, str], int] = {}
        for record in records:
            key = (record["country_code"], record["chamber"], record["filing_id"])
            counts[key] = counts.get(key, 0) + 1
        self._insert("alt.political_filings", [  # type: ignore[attr-defined]
            {**filings[key], "trade_count": count, "version": _version(now), "ingested_at": now}
            for key, count in counts.items()])
        if orphans:
            self._park_entities(provenance, orphans, "trade references an unknown filing")
        if unmatched:
            self._park(provenance, unmatched,  # type: ignore[attr-defined]
                       "ticker has no single listing on the transaction date")
        return WriteResult(len(records), len(orphans), len(records) - len(unmatched))

    def recent_filings(self, *, chamber: str, limit: int,
                       country_code: str = "US") -> list[FilingRef]:
        rows = self.client.query(
            "SELECT filing_id, filing_year, filing_url FROM alt.political_filings FINAL "
            "WHERE country_code = {country:String} AND chamber = {chamber:String} "
            "ORDER BY filing_date DESC, filing_id DESC LIMIT {limit:UInt32}",
            parameters={"country": country_code, "chamber": chamber, "limit": limit},
        ).result_rows
        return [FilingRef(_decoded_text(r[0]), int(r[1]), _decoded_text(r[2])) for r in rows]


__all__ = ["FILING_COLUMNS", "PoliticalSinkMixin"]
