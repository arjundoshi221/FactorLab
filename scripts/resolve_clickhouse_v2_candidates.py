"""Stage deterministic, unapproved canonical-ID candidates for ClickHouse v2.

This resolver is deliberately separate from migration SQL.  It may create
canonical dimension candidates and staging mappings, but it never approves a
mapping.  A human-reviewed approval step must insert a newer staging version
with ``approved_by`` and ``approved_at`` before any backfill can run.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

try:
    from migrate_clickhouse_v2 import SAFE_IDENTIFIER_RE, create_client, migration_lock
except ImportError:  # Imported as scripts.resolve_clickhouse_v2_candidates in tests.
    from scripts.migrate_clickhouse_v2 import SAFE_IDENTIFIER_RE, create_client, migration_lock


ENTITY_NAMESPACE = uuid.UUID("7cc91b95-bef0-4d70-81a4-23757e6218cd")
SECURITY_NAMESPACE = uuid.UUID("91529032-aef5-44f7-b33e-25f05f620d22")
LISTING_NAMESPACE = uuid.UUID("74360588-b425-4daf-bbff-9a601f8e97e4")
CONTRACT_NAMESPACE = uuid.UUID("f889fd4f-bc13-4450-ac2e-a477f0f07189")
LEGISLATOR_NAMESPACE = uuid.UUID("be05fde7-382d-4a7e-b84d-d7871c082f06")

EXCHANGE_CONTRACTS = {
    "NASDAQ": {"mic": "XNAS", "open": "09:30", "close": "16:00"},
    "NSE": {"mic": "XNSE", "open": "09:15", "close": "15:30"},
    "NYSE Arca": {"mic": "ARCX", "open": "09:30", "close": "16:00"},
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    return str(value)


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def canonical_ids(instrument: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    isin = _text(instrument.get("isin")).strip().upper()
    instrument_key = _text(instrument["instrument_key"])
    security_key = f"isin:{isin}" if isin else f"legacy:factorlab:ref_instruments:{instrument_key}"
    return (
        uuid.uuid5(ENTITY_NAMESPACE, security_key),
        uuid.uuid5(SECURITY_NAMESPACE, security_key),
        uuid.uuid5(LISTING_NAMESPACE, f"factorlab:ref_instruments:{instrument_key}"),
    )


def contract_id(contract_key: str) -> uuid.UUID:
    return uuid.uuid5(CONTRACT_NAMESPACE, f"factorlab:ref_contracts:{contract_key}")


def legislator_id(bioguide_id: str) -> uuid.UUID:
    return uuid.uuid5(LEGISLATOR_NAMESPACE, f"bioguide:{bioguide_id.upper()}")


def _query_source_rows(client: Any, source_database: str) -> dict[str, list[dict[str, Any]]]:
    if not SAFE_IDENTIFIER_RE.fullmatch(source_database):
        raise ValueError(f"unsafe source database identifier: {source_database!r}")
    queries = {
        "countries": f"""
            SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
            FROM {source_database}.ref_countries FINAL ORDER BY country_code
        """,
        "exchanges": f"""
            SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
            FROM {source_database}.ref_exchanges FINAL ORDER BY exchange_code
        """,
        "instruments": f"""
            SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
            FROM {source_database}.ref_instruments FINAL ORDER BY instrument_key
        """,
        "contracts": f"""
            SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
            FROM {source_database}.ref_contracts FINAL ORDER BY contract_key
        """,
        "legislators": f"""
            SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash
            FROM {source_database}.alt_political_legislators FINAL ORDER BY bioguide_id
        """,
    }
    return {name: _rows(client.query(query)) for name, query in queries.items()}


def _existing_ids(client: Any, table: str, column: str) -> set[uuid.UUID]:
    return {uuid.UUID(str(row[0])) for row in client.query(f"SELECT {column} FROM {table} FINAL").result_rows}


def _existing_crosswalks(client: Any) -> set[tuple[str, str, str, str, uuid.UUID]]:
    return {
        (_text(row[0]), _text(row[1]), _text(row[2]), _text(row[3]), uuid.UUID(str(row[4])))
        for row in client.query(
            "SELECT legacy_table, legacy_key, source_hash, target_kind, target_id "
            "FROM meta.migration_id_crosswalk FINAL"
        ).result_rows
    }


def _existing_enrichments(client: Any) -> set[tuple[str, str, str]]:
    return {
        (_text(row[0]), _text(row[1]), _text(row[2]))
        for row in client.query(
            "SELECT legacy_table, legacy_key, source_hash "
            "FROM meta.migration_reference_enrichment FINAL"
        ).result_rows
    }


def _insert(client: Any, table: str, rows: list[list[Any]], columns: list[str]) -> int:
    for start in range(0, len(rows), 5000):
        client.insert(table, rows[start : start + 5000], column_names=columns)
    return len(rows)


def build_plan(source: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {
        "countries": len(source["countries"]),
        "exchanges": len(source["exchanges"]),
        "currencies": len({_text(row["currency_code"]) for row in source["exchanges"]}),
        "issuer_entities": len(source["instruments"]),
        "securities": len(source["instruments"]),
        "listings": len(source["instruments"]),
        "contracts": len(source["contracts"]),
        "legislator_entities": len(source["legislators"]),
        "unapproved_crosswalks": (
            len(source["instruments"]) + len(source["contracts"]) + len(source["legislators"])
        ),
        "unapproved_reference_enrichments": len(source["countries"]) + len(source["exchanges"]),
    }


def stage_candidates(client: Any, source_database: str) -> dict[str, int]:
    source = _query_source_rows(client, source_database)
    unknown_exchanges = {
        _text(row["exchange_code"]) for row in source["exchanges"]
    } - EXCHANGE_CONTRACTS.keys()
    if unknown_exchanges:
        raise ValueError(f"exchange metadata is not configured for: {sorted(unknown_exchanges)}")

    now = datetime.now(UTC)
    candidate_version = time.time_ns()
    evidence = json.dumps(
        {
            "resolver": "factorlab-v2-candidate-resolver-v1",
            "status": "candidate_requires_human_approval",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    counts: dict[str, int] = {}

    country_currency = {
        _text(row["country_code"]): _text(row["currency_code"])
        for row in source["exchanges"]
    }
    existing_currencies = {
        _text(row[0]) for row in client.query("SELECT currency_code FROM ref.currencies FINAL").result_rows
    }
    currency_rows = [
        [currency, currency, currency != "INR", candidate_version, now]
        for currency in sorted(set(country_currency.values()) - existing_currencies)
    ]
    counts["currencies"] = _insert(
        client,
        "ref.currencies",
        currency_rows,
        ["currency_code", "name", "is_deliverable", "version", "ingested_at"],
    )

    existing_countries = {
        _text(row[0]) for row in client.query("SELECT country_code FROM ref.countries FINAL").result_rows
    }
    country_rows = []
    for row in source["countries"]:
        code = _text(row["country_code"])
        if code in existing_countries:
            continue
        region = {"asia": "apac"}.get(_text(row["region"]).lower(), _text(row["region"]).lower())
        country_rows.append(
            [
                code,
                _text(row["name"]),
                region,
                country_currency[code],
                _text(row["timezone"]),
                True,
                row["ingested_at"].date(),
                int(row["version"]),
                row["ingested_at"],
            ]
        )
    counts["countries"] = _insert(
        client,
        "ref.countries",
        country_rows,
        [
            "country_code", "name", "region", "default_currency", "timezone", "active",
            "first_active_at", "version", "ingested_at",
        ],
    )

    existing_exchanges = {
        _text(row[0]) for row in client.query("SELECT exchange_code FROM ref.exchanges FINAL").result_rows
    }
    exchange_rows = []
    for row in source["exchanges"]:
        code = _text(row["exchange_code"])
        if code in existing_exchanges:
            continue
        contract = EXCHANGE_CONTRACTS[code]
        sessions = json.dumps(
            {"regular": {"open": contract["open"], "close": contract["close"]}},
            separators=(",", ":"),
        )
        exchange_rows.append(
            [
                code, contract["mic"], _text(row["name"]), _text(row["country_code"]),
                _text(row["currency_code"]), _text(row["timezone"]), sessions, True,
                int(row["version"]), row["ingested_at"],
            ]
        )
    counts["exchanges"] = _insert(
        client,
        "ref.exchanges",
        exchange_rows,
        [
            "exchange_code", "mic", "name", "country_code", "currency_code", "timezone",
            "sessions", "active", "version", "ingested_at",
        ],
    )

    existing_sources = {
        _text(row[0]) for row in client.query("SELECT source_id FROM ref.sources FINAL").result_rows
    }
    source_names = {
        _text(row["source"])
        for group in ("countries", "exchanges", "instruments", "contracts", "legislators")
        for row in source[group]
    }
    source_rows = [
        [name, "migration", name, "none", ["*"], True, evidence, candidate_version, now]
        for name in sorted(source_names - existing_sources)
    ]
    counts["sources"] = _insert(
        client,
        "ref.sources",
        source_rows,
        [
            "source_id", "kind", "vendor", "auth_type", "countries", "active", "notes",
            "version", "ingested_at",
        ],
    )

    existing_entities = _existing_ids(client, "ref.entities", "entity_id")
    existing_securities = _existing_ids(client, "ref.securities", "security_id")
    existing_listings = _existing_ids(client, "ref.listings", "listing_id")
    entity_rows: list[list[Any]] = []
    security_rows: list[list[Any]] = []
    listing_rows: list[list[Any]] = []
    legacy_listing_ids: dict[uuid.UUID, uuid.UUID] = {}
    listing_candidates: list[tuple[dict[str, Any], uuid.UUID]] = []
    for row in source["instruments"]:
        entity, security, listing = canonical_ids(row)
        legacy_listing_ids[uuid.UUID(str(row["instrument_id"]))] = listing
        listing_candidates.append((row, listing))
        active = _text(row["status"]).lower() == "active"
        country = _text(row["country_code"])
        if entity not in existing_entities:
            entity_rows.append(
                [
                    entity, "issuer", _text(row["name"]), None, country, country, active,
                    row["first_seen"], row["last_seen"], int(row["version"]), row["ingested_at"],
                ]
            )
            existing_entities.add(entity)
        if security not in existing_securities:
            security_rows.append(
                [
                    security, entity, _text(row["asset_class"]).lower(),
                    _text(row.get("isin")) or None, None, None, "", _text(row["currency_code"]),
                    row["first_seen"], None, None, "", active, int(row["version"]), row["ingested_at"],
                ]
            )
            existing_securities.add(security)
        if listing not in existing_listings:
            exchange = _text(row["exchange_code"])
            listing_rows.append(
                [
                    listing, security, exchange, country, _text(row["trading_symbol"]), None,
                    EXCHANGE_CONTRACTS[exchange]["mic"], int(row["lot_size"]), row["tick_size"],
                    True, row["first_seen"], None if active else row["last_seen"], active,
                    int(row["version"]), row["ingested_at"],
                ]
            )
            existing_listings.add(listing)
    counts["issuer_entities"] = _insert(
        client, "ref.entities", entity_rows,
        [
            "entity_id", "entity_type", "legal_name", "lei", "country_of_domicile",
            "country_of_incorp", "active", "first_seen", "last_seen", "version", "ingested_at",
        ],
    )
    counts["securities"] = _insert(
        client, "ref.securities", security_rows,
        [
            "security_id", "entity_id", "security_type", "isin", "cusip", "figi",
            "share_class", "currency_code", "issue_date", "maturity_date", "sector_id",
            "sector_classification", "active", "version", "ingested_at",
        ],
    )
    counts["listings"] = _insert(
        client, "ref.listings", listing_rows,
        [
            "listing_id", "security_id", "exchange_code", "country_code", "trading_symbol",
            "local_symbol", "mic", "lot_size", "tick_size", "is_primary", "first_traded",
            "last_traded", "active", "version", "ingested_at",
        ],
    )

    existing_contracts = _existing_ids(client, "ref.contracts", "contract_id")
    contract_rows: list[list[Any]] = []
    contract_candidates: list[tuple[dict[str, Any], uuid.UUID]] = []
    for row in source["contracts"]:
        key = _text(row["contract_key"])
        target_id = contract_id(key)
        contract_candidates.append((row, target_id))
        if target_id in existing_contracts:
            continue
        underlying = legacy_listing_ids[uuid.UUID(str(row["instrument_id"]))]
        legacy_type = _text(row["contract_type"]).upper()
        target_type = {"FUT": "future", "CE": "call", "PE": "put"}.get(legacy_type)
        if target_type is None:
            raise ValueError(f"unsupported contract type {legacy_type!r} for {key}")
        exchange = _text(row["segment"]).split("_", 1)[0]
        country = next(
            _text(item["country_code"])
            for item in source["instruments"]
            if uuid.UUID(str(item["instrument_id"])) == uuid.UUID(str(row["instrument_id"]))
        )
        active = _text(row["status"]).lower() == "active"
        contract_rows.append(
            [
                target_id, underlying, exchange, country, target_type, row["expiry"],
                None if legacy_type == "FUT" else row["strike_price"],
                {"CE": "C", "PE": "P"}.get(legacy_type, ""), "", 1,
                int(row["lot_size"]), row["tick_size"], bool(row["weekly"]), active,
                int(row["version"]), row["ingested_at"],
            ]
        )
        existing_contracts.add(target_id)
    counts["contracts"] = _insert(
        client, "ref.contracts", contract_rows,
        [
            "contract_id", "underlying_listing_id", "exchange_code", "country_code",
            "contract_type", "expiry", "strike", "right", "exercise_style", "multiplier",
            "lot_size", "tick_size", "weekly", "active", "version", "ingested_at",
        ],
    )

    legislator_candidates: list[tuple[dict[str, Any], uuid.UUID]] = []
    legislator_rows: list[list[Any]] = []
    for row in source["legislators"]:
        bioguide = _text(row["bioguide_id"])
        target_id = legislator_id(bioguide)
        legislator_candidates.append((row, target_id))
        if target_id in existing_entities:
            continue
        legislator_rows.append(
            [
                target_id, "person_legislator", _text(row["official_full"]), None, "US", "US",
                bool(row["in_office"]), row["term_start"], row["term_end"], int(row["version"]),
                row["ingested_at"],
            ]
        )
        existing_entities.add(target_id)
    counts["legislator_entities"] = _insert(
        client, "ref.entities", legislator_rows,
        [
            "entity_id", "entity_type", "legal_name", "lei", "country_of_domicile",
            "country_of_incorp", "active", "first_seen", "last_seen", "version", "ingested_at",
        ],
    )

    existing_crosswalks = _existing_crosswalks(client)
    crosswalk_rows: list[list[Any]] = []
    candidates: Iterable[tuple[str, str, dict[str, Any], uuid.UUID]] = (
        [("ref_instruments", "instrument_key", row, target) for row, target in listing_candidates]
        + [("ref_contracts", "contract_key", row, target) for row, target in contract_candidates]
        + [
            ("alt_political_legislators", "bioguide_id", row, target)
            for row, target in legislator_candidates
        ]
    )
    for legacy_table, key_column, row, target in candidates:
        target_kind = {
            "ref_instruments": "listing",
            "ref_contracts": "contract",
            "alt_political_legislators": "entity",
        }[legacy_table]
        key = _text(row[key_column])
        source_hash = _text(row["source_hash"])
        marker = (legacy_table, key, source_hash, target_kind, target)
        if marker in existing_crosswalks:
            continue
        crosswalk_rows.append(
            [
                source_database, legacy_table, key, source_hash, target_kind, target, "", None,
                evidence, candidate_version, now,
            ]
        )
    counts["unapproved_crosswalks"] = _insert(
        client, "meta.migration_id_crosswalk", crosswalk_rows,
        [
            "legacy_database", "legacy_table", "legacy_key", "source_hash", "target_kind",
            "target_id", "approved_by", "approved_at", "evidence", "version", "ingested_at",
        ],
    )

    existing_enrichments = _existing_enrichments(client)
    enrichment_rows: list[list[Any]] = []
    for row in source["countries"]:
        key = _text(row["country_code"])
        source_hash = _text(row["source_hash"])
        if ("ref_countries", key, source_hash) in existing_enrichments:
            continue
        enrichment_rows.append(
            [
                source_database, "ref_countries", key, source_hash, "", "****",
                _text(row["timezone"]), "", "", country_currency[key], "", None,
                evidence, candidate_version, now,
            ]
        )
    for row in source["exchanges"]:
        key = _text(row["exchange_code"])
        source_hash = _text(row["source_hash"])
        if ("ref_exchanges", key, source_hash) in existing_enrichments:
            continue
        contract = EXCHANGE_CONTRACTS[key]
        enrichment_rows.append(
            [
                source_database, "ref_exchanges", key, source_hash, key, contract["mic"],
                _text(row["timezone"]), contract["open"], contract["close"],
                _text(row["currency_code"]), "", None, evidence, candidate_version, now,
            ]
        )
    counts["unapproved_reference_enrichments"] = _insert(
        client, "meta.migration_reference_enrichment", enrichment_rows,
        [
            "legacy_database", "legacy_table", "legacy_key", "source_hash", "exchange_code",
            "mic", "session_timezone", "regular_open", "regular_close", "currency_code",
            "approved_by", "approved_at", "evidence", "version", "ingested_at",
        ],
    )
    return counts


def report(client: Any) -> dict[str, int]:
    queries = {
        "candidate_entities": "SELECT count() FROM ref.entities FINAL",
        "candidate_securities": "SELECT count() FROM ref.securities FINAL",
        "candidate_listings": "SELECT count() FROM ref.listings FINAL",
        "candidate_contracts": "SELECT count() FROM ref.contracts FINAL",
        "unapproved_crosswalks": (
            "SELECT count() FROM meta.migration_id_crosswalk FINAL WHERE approved_at IS NULL"
        ),
        "approved_crosswalks": (
            "SELECT count() FROM meta.migration_id_crosswalk FINAL WHERE approved_at IS NOT NULL"
        ),
        "unapproved_reference_enrichments": (
            "SELECT count() FROM meta.migration_reference_enrichment FINAL WHERE approved_at IS NULL"
        ),
    }
    return {name: int(client.query(query).result_rows[0][0]) for name, query in queries.items()}


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    for name, count in counts.items():
        print(f"  {name}: {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", default="factorlab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan")
    stage = subparsers.add_parser("stage")
    stage.add_argument("--yes", action="store_true")
    subparsers.add_parser("report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = create_client()
    try:
        if args.command == "plan":
            _print_counts("Candidate resolver plan", build_plan(_query_source_rows(client, args.source_database)))
        elif args.command == "stage":
            if not args.yes:
                print("error: stage mutates candidate tables; rerun with --yes")
                return 2
            with migration_lock(client):
                _print_counts(
                    "Candidate rows staged (all mappings remain unapproved)",
                    stage_candidates(client, args.source_database),
                )
        elif args.command == "report":
            _print_counts("Candidate resolver status", report(client))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
