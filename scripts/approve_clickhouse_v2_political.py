"""Approve reviewed current-hash political corrections without changing prior decisions.

Existing filing and trade keys must retain every semantic correction field.
New trade keys require a verified archived-PDF evidence record; new filing keys
require their source filing-header evidence. This never guesses an asset match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

try:
    from migrate_clickhouse_v2 import create_client, migration_lock
except ImportError:
    from scripts.migrate_clickhouse_v2 import create_client, migration_lock


SEMANTIC_COLUMNS = (
    "amount_bucket_id", "amount_min", "amount_max", "amount_currency",
    "security_type", "normalized_legislator_name", "bioguide_id",
    "legislator_entity_id", "listing_id", "security_id", "entity_id",
    "contract_id", "bioguide_confidence", "asset_resolution_confidence",
)


def _rows(client: Any) -> tuple[list[str], list[dict[str, Any]]]:
    result = client.query("SELECT * FROM meta.migration_political_trade_enrichment FINAL")
    return list(result.column_names), [
        dict(zip(result.column_names, row, strict=True)) for row in result.result_rows
    ]


def reviewed_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prior: dict[tuple[str, str], dict[str, Any]] = {}
    pending = []
    for row in rows:
        key = str(row["legacy_table"]), str(row["legacy_key"])
        if row["approved_at"] is None:
            pending.append(row)
        elif key not in prior or row["version"] > prior[key]["version"]:
            prior[key] = row
    for row in pending:
        key = str(row["legacy_table"]), str(row["legacy_key"])
        previous = prior.get(key)
        if previous is not None:
            changed = [column for column in SEMANTIC_COLUMNS if row[column] != previous[column]]
            if changed:
                raise ValueError(f"changed approved correction for {key}: {changed}")
        evidence = json.loads(str(row["evidence"]))
        if key[0] == "alt_political_trades":
            if evidence.get("source") != "archived House Clerk PDF" or not evidence.get("archive_sha256"):
                raise ValueError(f"trade lacks PDF evidence: {key}")
        elif key[0] == "alt_political_house_filings":
            if evidence.get("source") != "legacy House filing header":
                raise ValueError(f"filing lacks source evidence: {key}")
        else:
            raise ValueError(f"unexpected political correction table: {key[0]}")
    return sorted(pending, key=lambda row: (
        str(row["legacy_table"]), str(row["legacy_key"]), str(row["source_hash"])
    ))


def approve(client: Any, approved_by: str) -> tuple[int, str]:
    columns, rows = _rows(client)
    pending = reviewed_candidates(rows)
    fingerprint = hashlib.sha256(json.dumps([
        [str(row[name]) for name in ("legacy_table", "legacy_key", "source_hash", *SEMANTIC_COLUMNS)]
        for row in pending
    ], separators=(",", ":")).encode()).hexdigest()
    if not pending:
        return 0, fingerprint
    now = datetime.now(UTC)
    version = time.time_ns()
    records = []
    for row in pending:
        approved = dict(row)
        evidence = json.loads(str(row["evidence"]))
        evidence["approval"] = {
            "approved_by": approved_by,
            "candidate_fingerprint": fingerprint,
            "basis": "unchanged prior decision or new source-backed PDF/filing",
        }
        approved.update(approved_by=approved_by, approved_at=now,
                        evidence=json.dumps(evidence, sort_keys=True),
                        version=version, ingested_at=now)
        records.append([approved[column] for column in columns])
    for start in range(0, len(records), 5000):
        client.insert("meta.migration_political_trade_enrichment",
                      records[start:start + 5000], column_names=columns)
    return len(records), fingerprint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not args.yes:
        parser.error("approval requires --yes")
    approved_by = args.approved_by.strip()
    if not approved_by:
        parser.error("--approved-by cannot be empty")
    client = create_client()
    try:
        with migration_lock(client):
            count, fingerprint = approve(client, approved_by)
        print(f"approved={count} fingerprint={fingerprint}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
