"""Canonical political reference, filing, and trade writes."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from factorlab.storage.canonical_ids import committee_id, legislator_id, political_trade_id
from factorlab.storage.clickhouse import _version
from factorlab.storage.political_clickhouse import (
    PoliticalClickHouseStorage,
    _name_key,
    _normalize_role,
)
from factorlab.storage.v2_india import V2IndiaStorage
from factorlab.storage.v2_reference import UnresolvedReference


class V2PoliticalClickHouseStorage(PoliticalClickHouseStorage):
    def __init__(self, storage: V2IndiaStorage) -> None:
        super().__init__(storage)

    def _insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        self.storage._insert_dicts(table, rows)

    def _run_id(self) -> uuid.UUID:
        run_id = self.storage._active_run_id
        if run_id is None:
            raise RuntimeError("start a political ingestion run before curated writes")
        return run_id

    def _entity_exists(self, entity_id: uuid.UUID) -> bool:
        result = self.client.query(
            "SELECT count() FROM ref.entities FINAL WHERE entity_id = {id:UUID}",
            parameters={"id": entity_id},
        )
        return int(result.result_rows[0][0]) == 1

    def sync_legislators(self, legislators: Sequence[Mapping[str, Any]], *, raw_id: Any):
        now = datetime.now(UTC)
        rows = []
        aliases = []
        terms = []
        lookup = {}
        for item in legislators:
            bioguide = str(item["id"]["bioguide"])
            name = item["name"]
            term = item["terms"][-1]
            official = name.get("official_full") or " ".join(
                str(value) for value in (name.get("first"), name.get("middle"),
                                         name.get("last"), name.get("suffix")) if value
            )
            entity_id = legislator_id(bioguide)
            lookup[bioguide] = {
                "bioguide_id": bioguide, "official_full": official,
                "party": term.get("party", ""), "state": term.get("state", ""),
                "entity_id": entity_id,
            }
            start = date.fromisoformat(term["start"])
            end = date.fromisoformat(term["end"])
            version = _version(now)
            rows.append({
                "entity_id": entity_id, "entity_type": "person_legislator",
                "legal_name": official, "lei": None,
                "country_of_domicile": "US", "country_of_incorp": "US",
                "active": True, "first_seen": start, "last_seen": end,
                "version": version, "ingested_at": now,
            })
            aliases.append({
                "alias_kind": "bioguide", "alias_value": bioguide,
                "scope_country": "US", "scope_exchange": None,
                "target_kind": "entity", "target_id": entity_id,
                "source": "congress_legislators", "valid_from": start,
                "valid_to": None, "confidence": "exact", "notes": "",
                "version": version, "ingested_at": now,
            })
            for historical_term in item["terms"]:
                term_start = date.fromisoformat(historical_term["start"])
                term_end = date.fromisoformat(historical_term["end"])
                chamber = {"rep": "house", "house": "house",
                           "sen": "senate", "senate": "senate"}.get(
                    str(historical_term.get("type", "")).lower()
                )
                if chamber is None:
                    continue
                terms.append({
                    "legislator_entity_id": entity_id,
                    "bioguide_id": bioguide,
                    "congress_number": (term_start.year - 1789) // 2 + 1,
                    "chamber": chamber,
                    "state": historical_term.get("state") or "",
                    "district": historical_term.get("district"),
                    "party": historical_term.get("party") or "",
                    "seat_class": historical_term.get("class"),
                    "term_start": term_start,
                    "term_end": term_end,
                    "in_office": term_end >= now.date(),
                    "source": "congress_legislators",
                    "raw_id": raw_id,
                    "as_of_time": now,
                    "version": version,
                    "ingested_at": now,
                })
        self._insert("ref.entities", rows)
        self._insert("ref.identifier_aliases", aliases)
        self._insert("ref.legislator_terms", terms)
        return lookup

    def sync_committees(self, committees: Sequence[Mapping[str, Any]], *,
                        raw_id: Any, congress_number: int):
        now = datetime.now(UTC)
        entities = []
        rows = []
        lookup = {}
        for committee in committees:
            parent = str(committee["thomas_id"])
            candidates = [(parent, None, committee, False)]
            candidates.extend((parent + sub["thomas_id"], parent, sub, True)
                              for sub in committee.get("subcommittees", []))
            for code, parent_code, item, subcommittee in candidates:
                name = str(item["name"])
                lookup[code] = name
                entity_id = committee_id(code)
                version = _version(now)
                entities.append({
                    "entity_id": entity_id, "entity_type": "committee",
                    "legal_name": name, "lei": None,
                    "country_of_domicile": "US", "country_of_incorp": "US",
                    "active": True, "first_seen": now.date(), "last_seen": now.date(),
                    "version": version, "ingested_at": now,
                })
                rows.append({
                    "committee_id": code, "committee_entity_id": entity_id,
                    "country_code": "US", "congress_number": congress_number,
                    "parent_committee_id": parent_code,
                    "chamber": str(committee.get("type") or "").lower(),
                    "name": name, "jurisdiction": item.get("jurisdiction") or "",
                    "url": item.get("url") or "", "is_subcommittee": subcommittee,
                    "effective_from": now.date(), "effective_to": None,
                    "source": "congress_legislators", "raw_id": raw_id,
                    "ingest_run_id": self._run_id(), "as_of_time": now,
                    "ingested_at": now, "version": version,
                })
        self._insert("ref.entities", entities)
        self._insert("alt.political_committees", rows)
        return lookup

    def sync_memberships(self, memberships: Mapping[str, Sequence[Mapping[str, Any]]], *,
                         legislator_lookup: Mapping[str, Mapping[str, Any]],
                         committee_lookup: Mapping[str, str], raw_id: Any,
                         snapshot_date: date, congress_number: int) -> int:
        now = datetime.now(UTC)
        rows = []
        for code, members in memberships.items():
            if code not in committee_lookup or not self._entity_exists(committee_id(code)):
                raise UnresolvedReference(f"committee unresolved: {code}")
            for member in members:
                bioguide = member.get("bioguide")
                if not bioguide:
                    continue
                if bioguide not in legislator_lookup or not self._entity_exists(legislator_id(bioguide)):
                    self.storage._identity_status(
                        source="congress_legislators", alias_kind="bioguide",
                        alias_value=bioguide, raw_id=raw_id,
                        reason=f"membership legislator unresolved: {bioguide}",
                    )
                    continue
                rows.append({
                    "country_code": "US", "congress_number": congress_number,
                    "committee_id": code, "committee_entity_id": committee_id(code),
                    "bioguide_id": bioguide,
                    "legislator_entity_id": legislator_id(bioguide),
                    "role": _normalize_role(member.get("title", "")),
                    "majority_status": member.get("party") or "",
                    "rank": int(member.get("rank") or 0),
                    "effective_from": snapshot_date, "effective_to": None,
                    "source": "congress_legislators", "raw_id": raw_id,
                    "ingest_run_id": self._run_id(), "as_of_time": now,
                    "ingested_at": now, "version": _version(now),
                })
        self._insert("alt.political_committee_memberships", rows)
        return len(rows)

    def write_house_filings(self, filings: Sequence[Mapping[str, Any]], *,
                            raw_id: Any, name_resolver: Mapping[tuple[str, str], str]) -> int:
        now = datetime.now(UTC)
        rows = []
        for filing in filings:
            bioguide = name_resolver.get(_name_key(
                filing["filer_first_name"], filing["filer_last_name"]
            ))
            entity_id = legislator_id(bioguide) if bioguide else None
            if entity_id and not self._entity_exists(entity_id):
                entity_id = None
            rows.append({
                "filing_id": filing["filing_id"], "country_code": "US",
                "chamber": "house", "filing_type": filing["filing_type"],
                "filing_year": filing["filing_year"], "filing_date": filing["filing_date"],
                "filer_name_raw": filing["filer_name_raw"],
                "bioguide_id": bioguide if entity_id else None,
                "legislator_entity_id": entity_id,
                "bioguide_confidence": "exact" if entity_id else "unresolved",
                "filing_url": filing["filing_url"], "amends_filing_id": None,
                "original_filing_id": None, "amendment_seq": 0,
                "is_amended": False, "is_amendment": False, "trade_count": 0,
                "source": "house_clerk_filing_index", "source_channel": "house_clerk_ptr",
                "raw_id": raw_id, "ingest_run_id": self._run_id(),
                "as_of_time": now, "ingested_at": now, "version": _version(now),
            })
        self._insert("alt.political_filings", rows)
        return len(rows)

    def write_trades(self, trades: Sequence[Mapping[str, Any]], *,
                     filing: Mapping[str, Any], raw_id: Any,
                     bioguide_id: str | None) -> int:
        now = datetime.now(UTC)
        filing_result = self.client.query(
            "SELECT * FROM alt.political_filings FINAL "
            "WHERE filing_id = {filing_id:String} AND country_code = 'US'",
            parameters={"filing_id": filing["filing_id"]},
        )
        if len(filing_result.result_rows) != 1:
            raise UnresolvedReference(f"filing unresolved: {filing['filing_id']}")
        legislator_entity = legislator_id(bioguide_id) if bioguide_id else None
        if legislator_entity and not self._entity_exists(legislator_entity):
            legislator_entity = None
        rows = []
        for trade in trades:
            ticker = str(trade.get("ticker") or "").strip().upper()
            listing = None
            if ticker:
                candidates = self.client.query(
                    "SELECT DISTINCT l.listing_id, l.security_id, s.entity_id, "
                    "s.security_type FROM ref.listings AS l FINAL "
                    "INNER JOIN ref.securities AS s FINAL ON s.security_id = l.security_id "
                    "WHERE l.trading_symbol = {ticker:String} "
                    "AND l.country_code = 'US' "
                    "AND (l.first_traded IS NULL OR l.first_traded <= {day:Date}) "
                    "AND (l.last_traded IS NULL OR l.last_traded >= {day:Date})",
                    parameters={"ticker": ticker, "day": trade["transaction_date"]},
                ).result_rows
                if len(candidates) == 1:
                    listing = candidates[0]
                    self.storage._identity_status(
                        source="house_clerk_ptr", alias_kind="ticker",
                        alias_value=ticker, raw_id=raw_id,
                        target_kind="listing", target_id=listing[0],
                    )
                else:
                    self.storage._identity_status(
                        source="house_clerk_ptr", alias_kind="ticker",
                        alias_value=ticker, raw_id=raw_id,
                        reason=f"ticker has {len(candidates)} current canonical matches",
                    )
            rows.append({
                "political_trade_id": political_trade_id(str(trade["trade_key"])),
                "country_code": "US", "chamber": "house",
                "filing_id": filing["filing_id"], "filing_url": filing["filing_url"],
                "filing_date": filing["filing_date"],
                "transaction_date": trade["transaction_date"],
                "notification_date": trade.get("notification_date"),
                "legislator_name_raw": filing["filer_name_raw"],
                "bioguide_id": bioguide_id if legislator_entity else None,
                "legislator_entity_id": legislator_entity,
                "bioguide_confidence": "exact" if legislator_entity else "unresolved",
                "ticker_raw": ticker or None,
                "asset_name_raw": trade["asset_name_raw"],
                "filing_asset_type_code": trade["asset_type_code"],
                "security_type": listing[3] if listing else "other",
                "listing_id": listing[0] if listing else None,
                "contract_id": None, "security_id": listing[1] if listing else None,
                "entity_id": listing[2] if listing else None,
                "resolution_confidence": "exact" if listing else "unresolved",
                "transaction_type": trade["transaction_type"],
                "owner_code": trade["owner_code"], "filer_type": trade["filer_type"],
                "amount_bucket_id": trade.get("amount_str") or "",
                "amount_min": trade.get("amount_min"),
                "amount_max": trade.get("amount_max"), "amount_currency": "USD",
                "amount_str_raw": trade.get("amount_str") or "",
                "source": "house_clerk_ptr", "source_channel": "house_clerk_ptr",
                "parser_version": "house_ptr_v1", "raw_id": raw_id,
                "ingest_run_id": self._run_id(), "as_of_time": now,
                "ingested_at": now, "version": _version(now),
            })
        self._insert("alt.political_trades", rows)
        if rows:
            record = dict(zip(filing_result.column_names,
                              filing_result.result_rows[0], strict=True))
            record.update(trade_count=len(rows), version=_version(now), ingested_at=now)
            self._insert("alt.political_filings", [record])
        return len(rows)
