"""ClickHouse writers for the denormalized US political-data slice."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from factorlab.storage.clickhouse import ClickHouseStorage


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _version(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1_000_000)


class PoliticalClickHouseStorage:
    """Write denormalized congressional reference, filing, and trade rows."""

    def __init__(self, storage: ClickHouseStorage) -> None:
        self.storage = storage
        self.client = storage.client

    def sync_legislators(
        self,
        legislators: Sequence[Mapping[str, Any]],
        *,
        raw_id: Any,
    ) -> dict[str, dict[str, Any]]:
        timestamp = _utc_now()
        version = _version(timestamp)
        rows = []
        lookup = {}
        for legislator in legislators:
            ids = legislator["id"]
            name = legislator["name"]
            bio = legislator.get("bio", {})
            term = legislator["terms"][-1]
            bioguide_id = ids["bioguide"]
            row_data = {
                "bioguide_id": bioguide_id,
                "official_full": name.get("official_full")
                or " ".join(
                    part
                    for part in (
                        name.get("first"),
                        name.get("middle"),
                        name.get("last"),
                        name.get("suffix"),
                    )
                    if part
                ),
                "party": term.get("party", ""),
                "state": term.get("state", ""),
            }
            lookup[bioguide_id] = row_data
            rows.append(
                [
                    bioguide_id,
                    name.get("first", ""),
                    name.get("middle"),
                    name.get("last", ""),
                    name.get("suffix"),
                    row_data["official_full"],
                    date.fromisoformat(bio["birthday"])
                    if bio.get("birthday")
                    else None,
                    bio.get("gender", ""),
                    term.get("type", ""),
                    term.get("state", ""),
                    term.get("district"),
                    term.get("party", ""),
                    date.fromisoformat(term["start"]),
                    date.fromisoformat(term["end"]),
                    True,
                    ids.get("fec", []),
                    ids.get("govtrack"),
                    ids.get("opensecrets"),
                    "congress_legislators",
                    raw_id,
                    version,
                    timestamp,
                ]
            )
        if rows:
            self.client.insert(
                "alt_political_legislators",
                rows,
                column_names=[
                    "bioguide_id", "first_name", "middle_name", "last_name",
                    "suffix", "official_full", "birthday", "gender", "chamber",
                    "state", "district", "party", "term_start", "term_end",
                    "in_office", "fec_candidate_ids", "govtrack_id",
                    "opensecrets_id", "source", "raw_id", "version",
                    "ingested_at",
                ],
            )
        return lookup

    def sync_committees(
        self,
        committees: Sequence[Mapping[str, Any]],
        *,
        raw_id: Any,
    ) -> dict[str, str]:
        timestamp = _utc_now()
        version = _version(timestamp)
        rows = []
        lookup = {}
        for committee in committees:
            committee_id = committee["thomas_id"]
            name = committee["name"]
            lookup[committee_id] = name
            rows.append(
                [
                    committee_id, None, committee.get("type", ""), name,
                    committee.get("jurisdiction", ""), committee.get("url", ""),
                    False, True, "congress_legislators", raw_id, version,
                    timestamp,
                ]
            )
            for subcommittee in committee.get("subcommittees", []):
                subcommittee_id = committee_id + subcommittee["thomas_id"]
                subcommittee_name = subcommittee["name"]
                lookup[subcommittee_id] = subcommittee_name
                rows.append(
                    [
                        subcommittee_id, committee_id, committee.get("type", ""),
                        subcommittee_name, subcommittee.get("jurisdiction", ""),
                        subcommittee.get("url", ""), True, True,
                        "congress_legislators", raw_id, version, timestamp,
                    ]
                )
        if rows:
            self.client.insert(
                "alt_political_committees",
                rows,
                column_names=[
                    "committee_id", "parent_committee_id", "chamber", "name",
                    "jurisdiction", "url", "is_subcommittee", "is_current",
                    "source", "raw_id", "version", "ingested_at",
                ],
            )
        return lookup

    def sync_memberships(
        self,
        memberships: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        legislator_lookup: Mapping[str, Mapping[str, Any]],
        committee_lookup: Mapping[str, str],
        raw_id: Any,
        snapshot_date: date,
        congress_number: int,
    ) -> int:
        timestamp = _utc_now()
        version = _version(timestamp)
        rows = []
        for committee_id, members in memberships.items():
            for member in members:
                bioguide_id = member.get("bioguide")
                if not bioguide_id:
                    continue
                legislator = legislator_lookup.get(bioguide_id, {})
                role_raw = member.get("title", "")
                rows.append(
                    [
                        snapshot_date, congress_number, committee_id,
                        committee_lookup.get(committee_id, ""), bioguide_id,
                        legislator.get("official_full") or member.get("name", ""),
                        legislator.get("party", ""), legislator.get("state", ""),
                        member.get("party", ""), _normalize_role(role_raw),
                        role_raw, int(member.get("rank") or 0),
                        "congress_legislators", raw_id, version, timestamp,
                    ]
                )
        if rows:
            self.client.insert(
                "alt_political_committee_memberships",
                rows,
                column_names=[
                    "snapshot_date", "congress_number", "committee_id",
                    "committee_name", "bioguide_id", "member_name",
                    "political_party", "state", "majority_status", "role",
                    "role_raw", "rank", "source", "raw_id", "version",
                    "ingested_at",
                ],
            )
        return len(rows)

    def write_house_filings(
        self,
        filings: Sequence[Mapping[str, Any]],
        *,
        raw_id: Any,
        name_resolver: Mapping[tuple[str, str], str],
    ) -> int:
        timestamp = _utc_now()
        version = _version(timestamp)
        rows = []
        for filing in filings:
            state, district = _state_district(filing["state_district_raw"])
            name_key = _name_key(
                filing["filer_first_name"],
                filing["filer_last_name"],
            )
            rows.append(
                [
                    filing["filing_id"], filing["filing_year"],
                    filing["filing_type"], filing["filing_date"],
                    filing["filer_prefix"], filing["filer_first_name"],
                    filing["filer_last_name"], filing["filer_suffix"],
                    filing["filer_name_raw"], name_resolver.get(name_key),
                    state, district, filing["state_district_raw"],
                    filing["filing_url"], "house_clerk_filing_index", raw_id,
                    version, timestamp, timestamp,
                ]
            )
        if rows:
            self.client.insert(
                "alt_political_house_filings",
                rows,
                column_names=[
                    "filing_id", "filing_year", "filing_type", "filing_date",
                    "filer_prefix", "filer_first_name", "filer_last_name",
                    "filer_suffix", "filer_name_raw", "bioguide_id", "state",
                    "district", "state_district_raw", "filing_url", "source",
                    "raw_id", "version", "as_of_time", "ingested_at",
                ],
            )
        return len(rows)

    def write_trades(
        self,
        trades: Sequence[Mapping[str, Any]],
        *,
        filing: Mapping[str, Any],
        raw_id: Any,
        bioguide_id: str | None,
    ) -> int:
        timestamp = _utc_now()
        version = _version(timestamp)
        state, district = _state_district(filing["state_district_raw"])
        rows = []
        for trade in trades:
            rows.append(
                [
                    trade["trade_key"], "US", "house", filing["filing_id"],
                    filing["filing_year"], filing["filing_date"],
                    filing["filing_url"], bioguide_id,
                    filing["filer_name_raw"], state, district,
                    trade["owner_code"], trade["filer_type"],
                    trade["asset_name_raw"], trade["ticker"],
                    trade["asset_type_code"], trade["transaction_type"],
                    trade["transaction_date"], trade["notification_date"],
                    trade["amount_str"], trade["amount_min"],
                    trade["amount_max"], "house_clerk_ptr", raw_id,
                    "house_ptr_v1", timestamp, timestamp, version,
                ]
            )
        if rows:
            self.client.insert(
                "alt_political_trades",
                rows,
                column_names=[
                    "trade_key", "country_code", "chamber", "filing_id",
                    "filing_year", "filing_date", "filing_url", "bioguide_id",
                    "legislator_name", "state", "district", "owner_code",
                    "filer_type", "asset_name_raw", "ticker",
                    "asset_type_code", "transaction_type", "transaction_date",
                    "notification_date", "amount_str", "amount_min",
                    "amount_max", "source", "raw_id", "parser_version",
                    "as_of_time", "ingested_at", "version",
                ],
            )
        return len(rows)


def build_name_resolver(
    legislators: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    resolver = {}
    for legislator in legislators:
        name = legislator["name"]
        resolver[_name_key(name.get("first", ""), name.get("last", ""))] = (
            legislator["id"]["bioguide"]
        )
    return resolver


def resolve_filing_bioguide(
    filing: Mapping[str, Any],
    resolver: Mapping[tuple[str, str], str],
) -> str | None:
    return resolver.get(
        _name_key(
            filing["filer_first_name"],
            filing["filer_last_name"],
        )
    )


def _name_key(first_name: str, last_name: str) -> tuple[str, str]:
    first = re.sub(r"[^a-z]", "", first_name.lower().split()[0])
    last = re.sub(r"[^a-z]", "", last_name.lower())
    return first, last


def _state_district(value: str) -> tuple[str, int | None]:
    match = re.match(r"^([A-Z]{2})(\d{1,2})?$", value)
    if not match:
        return "", None
    return match.group(1), int(match.group(2)) if match.group(2) else None


def _normalize_role(title: str) -> str:
    normalized = title.lower().replace("-", " ").strip()
    if "ranking" in normalized:
        return "ranking_member"
    if "vice" in normalized and "chair" in normalized:
        return "vice_chair"
    if "chair" in normalized:
        return "chair"
    return "member"

