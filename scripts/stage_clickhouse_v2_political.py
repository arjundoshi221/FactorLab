"""Stage unapproved Wave 4 political corrections from archived House PDFs.

This command never approves corrections or starts the migration backfill.  A
legacy trade is staged only when a parsed PDF row matches its filing,
transaction, ticker, asset name, and amount, or one of four exact, reviewed
parser-artifact signatures matches. Ambiguous or missing matches are reported
for review, not silently guessed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any

import pdfplumber

try:
    from factorlab.countries.us.political.house_clerk import parser as house_parser
except ImportError:
    parser_path = os.environ.get("FACTORLAB_HOUSE_PARSER_PATH")
    if not parser_path:
        raise
    spec = importlib.util.spec_from_file_location("house_parser", parser_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load House parser from {parser_path}")
    house_parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(house_parser)

try:
    from migrate_clickhouse_v2 import SAFE_IDENTIFIER_RE, create_client, migration_lock
except ImportError:
    from scripts.migrate_clickhouse_v2 import SAFE_IDENTIFIER_RE, create_client, migration_lock


COLUMNS = [
    "legacy_database", "legacy_table", "legacy_key", "source_hash", "amount_bucket_id",
    "amount_min", "amount_max", "amount_currency", "security_type",
    "normalized_legislator_name", "bioguide_id", "legislator_entity_id", "listing_id",
    "security_id", "entity_id", "contract_id", "bioguide_confidence",
    "asset_resolution_confidence", "approved_by", "approved_at", "evidence",
    "version", "ingested_at",
]
TRANSACTION_TYPES = {
    "P": {"purchase"},
    # The PDF parser collapses both sale variants to S; legacy retains partial.
    "S": {"sale_full", "sale_partial"},
    "E": {"exchange"},
}
AMOUNT_BUCKET_IDS = {
    "$1,001 - $15,000": "$1K-$15K",
    "$15,001 - $50,000": "$15K-$50K",
    "$50,001 - $100,000": "$50K-$100K",
    "$100,001 - $250,000": "$100K-$250K",
    "$250,001 - $500,000": "$250K-$500K",
    "$500,001 - $1,000,000": "$500K-$1M",
    "$1,000,001 - $5,000,000": "$1M-$5M",
    "$5,000,001 - $25,000,000": "$5M-$25M",
    "$25,000,001 - $50,000,000": "$25M-$50M",
    "Over $50,000,000": "$50M+",
}
OFFICIAL_HOUSE_ALIASES = {
    ("Hon. April McClain Delaney", "MD"): (
        "M001232", "April McClain Delaney", "https://clerk.house.gov/Members/M001232"
    ),
    ("Hon. Richard Dean Dr McCormick", "GA"): (
        "M001218", "Richard McCormick", "https://clerk.house.gov/Members/M001218"
    ),
    ("Hon. Scott Scott Franklin", "FL"): (
        "F000472", "Scott Franklin", "https://clerk.house.gov/Members/F000472"
    ),
    ("Hon. Daniel Crenshaw", "TX"): (
        "C001120", "Dan Crenshaw", "https://clerk.house.gov/Members/C001120"
    ),
    ("Hon. Richard W. Allen", "GA"): (
        "A000372", "Rick W. Allen", "https://clerk.house.gov/Members/A000372"
    ),
}
# Exact, source-specific parser artifacts reviewed against the archived PDF.
# These are not fuzzy rules: every legacy and PDF field below must still agree.
REVIEWED_PDF_MISMATCHES = {
    "74052a4ca3a3985b796f4f07d0908caade7f6152ffb5ad98e60246cd43f3e140": {
        "raw_id": "591fcbf8-8b5e-47c6-925f-fda5ab92c78e",
        "legacy_ticker": "FUND",
        "legacy_asset": "Lord Abbett Short Duration High Yield I (LSYIX) bond",
        "date": "08/26/2026", "type": "P", "amount": "$1,001 - $15,000",
        "pdf_ticker": "LSYIX",
        "pdf_asset": "Lord Abbett Short Duration High Yield I bond fund",
    },
    "ae5f7d579824f89b9bdee4008c880eac9c2e06aeeb1509fc27d26e1a5f86807e": {
        "raw_id": "a9d461d2-db06-4860-b9fb-06a82d7d058d",
        "legacy_ticker": "FAS", "legacy_asset": "",
        "date": "06/01/2026", "type": "S", "amount": "$1,001 - $15,000",
        "pdf_ticker": "", "pdf_asset": "FAS",
    },
    "c1490b84f9aaec2215377f8f91ae0fafc90c1c872e102cd6f41f0f0eff6d109d": {
        "raw_id": "9f9df184-7eaa-47df-9e2c-d14d9bab8855",
        "legacy_ticker": "BILL", "legacy_asset": "U.S. Treasury",
        "date": "07/28/2026", "type": "S", "amount": "$15,001 - $50,000",
        "pdf_ticker": "", "pdf_asset": "U.S. Treasury Bill",
    },
    "e3ca6d39d58e2ffe783926629cb39e8371d15e183da3e3de952d349418981c69": {
        "raw_id": "f24a893f-0bc3-48e8-949f-da0a097eae1f",
        "legacy_ticker": "V",
        "legacy_asset": (
            "Visa Inc. ID Owner Asset Transaction Date Notification Amount Cap. Type "
            "Date Gains > $200?"
        ),
        "date": "07/30/2026", "type": "S", "amount": "$1,001 - $15,000",
        "pdf_ticker": "", "pdf_asset": "Visa Inc. (V)",
    },
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    return str(value)


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def resolve_legislator(
    row: dict[str, Any], name_column: str, legislators: dict[str, Any]
) -> tuple[str | None, Any | None, str, str, dict[str, Any]]:
    """Use only a source bioguide or an explicit official House alias."""
    source_id = _text(row.get("bioguide_id"))
    raw_name = _text(row[name_column]).strip()
    if source_id:
        entity = legislators.get(source_id)
        return source_id, entity, "exact" if entity else "unresolved", raw_name, {}
    alias = OFFICIAL_HOUSE_ALIASES.get((raw_name, _text(row.get("state"))))
    if alias is None or alias[0] not in legislators:
        return None, None, "unresolved", raw_name, {}
    bioguide, official_name, profile_url = alias
    evidence = {
        "identity_source": profile_url,
        "identity_alias": raw_name,
        "identity_review": "House Clerk alias candidate; confirm filing identity",
    }
    if bioguide == "M001218":
        evidence["district_note"] = (
            "legacy GA-6; current GA-7; GA-6 recorded in Clerk 118th directory"
        )
        evidence["historical_district_source"] = (
            "https://clerk.house.gov/member_info/TTD-118.pdf"
        )
    return bioguide, legislators[bioguide], "high", official_name, evidence


def parse_archive(body: bytes) -> list[dict[str, Any]]:
    """Parse the archived response, preserving the original bytes as evidence."""
    payload = gzip.decompress(body) if body.startswith(b"\x1f\x8b") else body
    if not payload.startswith(b"%PDF"):
        raise ValueError("archived response is not a PDF")
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        content = "\n".join((page.extract_text() or "") for page in pdf.pages)
    chunks = house_parser.TRAILER_RE.split(house_parser._clean_text(content))
    return [trade for chunk in chunks[:-1] if (trade := house_parser._extract_trade(chunk))]


def exact_pdf_match(
    legacy: dict[str, Any], parsed_trades: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the sole exact source match; duplicates are intentionally rejected."""
    matches, _ = pdf_match_diagnostics(legacy, parsed_trades)
    return matches[0] if len(matches) == 1 else None


def reviewed_pdf_match(
    legacy: dict[str, Any], parsed_trades: list[dict[str, Any]]
) -> dict[str, Any] | None:
    expected = REVIEWED_PDF_MISMATCHES.get(_text(legacy["trade_key"]))
    if expected is None:
        return None
    month, day, year = map(int, expected["date"].split("/"))
    if (
        _text(legacy["raw_id"]) != expected["raw_id"]
        or _text(legacy["ticker"]) != expected["legacy_ticker"]
        or _text(legacy["asset_name_raw"]) != expected["legacy_asset"]
        or _text(legacy["amount_str"]) != expected["amount"]
        or legacy["transaction_date"] != date(year, month, day)
        or _text(legacy["transaction_type"]) not in TRANSACTION_TYPES[expected["type"]]
    ):
        return None
    matches = [
        parsed for parsed in parsed_trades
        if _text(parsed["tx_date"]) == expected["date"]
        and _text(parsed["tx_type"]) == expected["type"]
        and _text(parsed["amount_str"]) == expected["amount"]
        and _text(parsed["ticker"]) == expected["pdf_ticker"]
        and _text(parsed["asset_name_raw"]) == expected["pdf_asset"]
    ]
    return matches[0] if len(matches) == 1 else None


def supported_pdf_match(
    legacy: dict[str, Any], parsed_trades: list[dict[str, Any]]
) -> tuple[dict[str, Any], int] | None:
    """Accept duplicate rows only when every correction field agrees."""
    matches, reason = pdf_match_diagnostics(legacy, parsed_trades)
    if reason == "unique_exact_match":
        return matches[0], 1
    if reason == "duplicate_exact_pdf_rows":
        corrections = {
            (p["amount_min"], p["amount_max"], _text(p["asset_type_code"]))
            for p in matches
        }
        return (matches[0], len(matches)) if len(corrections) == 1 else None
    if reason != "ticker_mismatch":
        return None
    asset = _text(legacy["asset_name_raw"])
    ticker = _text(legacy["ticker"])
    if not asset or not ticker:
        return None
    suffix_assets = {f"{asset} {ticker}", f"{asset} ({ticker})"}
    suffix_matches = []
    for parsed in parsed_trades:
        try:
            month, day, year = map(int, parsed["tx_date"].split("/"))
            same_date = date(year, month, day) == legacy["transaction_date"]
            same_type = _text(legacy["transaction_type"]) in TRANSACTION_TYPES[parsed["tx_type"]]
        except (KeyError, ValueError):
            continue
        if (same_date and same_type and _text(parsed["ticker"]) == ""
                and _text(parsed["amount_str"]) == _text(legacy["amount_str"])
                and _text(parsed["asset_name_raw"]) in suffix_assets):
            suffix_matches.append(parsed)
    return (suffix_matches[0], 1) if len(suffix_matches) == 1 else None


def pdf_match_diagnostics(
    legacy: dict[str, Any], parsed_trades: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    """Explain the first match dimension that fails, without making a fuzzy match."""
    matches = []
    for parsed in parsed_trades:
        try:
            month, day, year = map(int, parsed["tx_date"].split("/"))
            transaction_date = date(year, month, day)
            transaction_types = TRANSACTION_TYPES[parsed["tx_type"]]
        except (KeyError, ValueError):
            continue
        if (
            transaction_date == legacy["transaction_date"]
            and _text(legacy["transaction_type"]) in transaction_types
        ):
            matches.append(parsed)
    if not matches:
        return [], "date_or_type_mismatch"
    matches = [p for p in matches if _text(p["ticker"]) == _text(legacy["ticker"])]
    if not matches:
        return [], "ticker_mismatch"
    matches = [p for p in matches if _text(p["asset_name_raw"]) == _text(legacy["asset_name_raw"])]
    if not matches:
        return [], "asset_name_mismatch"
    matches = [p for p in matches if _text(p["amount_str"]) == _text(legacy["amount_str"])]
    if not matches:
        return [], "amount_mismatch"
    if len(matches) > 1:
        return matches, "duplicate_exact_pdf_rows"
    return matches, "unique_exact_match"


def _source_rows(client: Any, source_database: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not SAFE_IDENTIFIER_RE.fullmatch(source_database):
        raise ValueError(f"unsafe source database identifier: {source_database!r}")
    filings = _rows(client.query(
        f"SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash "
        f"FROM {source_database}.alt_political_house_filings FINAL ORDER BY filing_id"
    ))
    trades = _rows(client.query(
        f"SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash "
        f"FROM {source_database}.alt_political_trades FINAL ORDER BY trade_key"
    ))
    return filings, trades


def _archive_rows(client: Any, source_database: str) -> dict[str, tuple[bytes, str]]:
    result = client.query(
        f"SELECT raw_id, hex(response_body), response_sha256 "
        f"FROM {source_database}.raw_http_archive "
        "WHERE raw_id IN (SELECT DISTINCT raw_id FROM "
        f"{source_database}.alt_political_trades FINAL) AND content_type = 'application/pdf'"
    )
    archives: dict[str, tuple[bytes, str]] = {}
    for raw_id, body, source_hash in result.result_rows:
        key = _text(raw_id)
        if key in archives:
            raise ValueError(f"duplicate archived PDF raw_id: {key}")
        compressed = bytes.fromhex(_text(body))
        payload = gzip.decompress(compressed) if compressed.startswith(b"\x1f\x8b") else compressed
        if hashlib.sha256(payload).hexdigest() != _text(source_hash):
            raise ValueError(f"archived PDF hash mismatch: {key}")
        archives[key] = (compressed, _text(source_hash))
    return archives


def build_candidates(
    filings: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    archives: dict[str, tuple[bytes, str]],
    legislators: dict[str, Any],
    source_database: str,
    now: datetime,
    version: int,
    review_examples: list[dict[str, Any]] | None = None,
) -> tuple[list[list[Any]], Counter[str]]:
    candidates: list[list[Any]] = []
    counts: Counter[str] = Counter()
    parsed_archives: dict[str, list[dict[str, Any]]] = {}
    for raw_id, (body, _) in archives.items():
        try:
            parsed_archives[raw_id] = parse_archive(body)
            counts["pdf_archives_parsed"] += 1
        except (ValueError, OSError, gzip.BadGzipFile):
            counts["pdf_archives_failed"] += 1
    for filing in filings:
        bioguide, entity, identity_confidence, normalized_name, identity_evidence = (
            resolve_legislator(filing, "filer_name_raw", legislators)
        )
        evidence = json.dumps({
            "source": "legacy House filing header",
            "raw_id": _text(filing["raw_id"]),
            "status": "candidate_requires_review",
            **identity_evidence,
        }, sort_keys=True)
        candidates.append([
            source_database, "alt_political_house_filings", _text(filing["filing_id"]),
            _text(filing["source_hash"]), "not_applicable", None, None, "USD", "not_applicable",
            normalized_name, bioguide, entity,
            None, None, None, None, identity_confidence, "unresolved",
            "", None, evidence, version, now,
        ])
        counts["filing_candidates"] += 1
        if identity_evidence:
            counts["filing_identity_alias_candidates"] += 1
    for trade in trades:
        raw_id = _text(trade["raw_id"])
        supported = supported_pdf_match(trade, parsed_archives.get(raw_id, []))
        reviewed = False
        if supported is None:
            reviewed_match = reviewed_pdf_match(trade, parsed_archives.get(raw_id, []))
            if reviewed_match is not None:
                supported = reviewed_match, 1
                reviewed = True
        if supported is None:
            counts["trade_needs_review"] += 1
            counts[f"review_archive:{raw_id}"] += 1
            if raw_id not in archives:
                counts["review_missing_archive"] += 1
            elif not parsed_archives.get(raw_id):
                counts["review_pdf_unparsed"] += 1
            else:
                _, reason = pdf_match_diagnostics(trade, parsed_archives[raw_id])
                counts[f"review_{reason}"] += 1
                if review_examples is not None and sum(
                    example["reason"] == reason for example in review_examples
                ) < 10:
                    same_ticker = [
                        p for p in parsed_archives[raw_id]
                        if _text(p["ticker"]) == _text(trade["ticker"])
                    ]
                    same_asset = [
                        p for p in parsed_archives[raw_id]
                        if _text(p["asset_name_raw"]) == _text(trade["asset_name_raw"])
                    ]
                    related_rows = sorted(
                        parsed_archives[raw_id],
                        key=lambda p: SequenceMatcher(
                            None,
                            _text(trade["asset_name_raw"]).casefold(),
                            _text(p["asset_name_raw"]).casefold(),
                        ).ratio(),
                        reverse=True,
                    )
                    review_examples.append({
                        "reason": reason,
                        "raw_id": raw_id,
                        "trade_key": _text(trade["trade_key"]),
                        "filing_id": _text(trade["filing_id"]),
                        "legacy_date": _text(trade["transaction_date"]),
                        "legacy_type": _text(trade["transaction_type"]),
                        "ticker": _text(trade["ticker"]),
                        "legacy_amount": _text(trade["amount_str"]),
                        "legacy_asset": _text(trade["asset_name_raw"])[:120],
                        "same_date_type_pdf_rows": [
                            {"asset": _text(p["asset_name_raw"])[:120],
                             "date": p["tx_date"], "type": p["tx_type"],
                             "amount": p["amount_str"], "ticker": p["ticker"]}
                            for p in parsed_archives[raw_id]
                            if (
                                date(
                                    int(p["tx_date"].split("/")[2]),
                                    int(p["tx_date"].split("/")[0]),
                                    int(p["tx_date"].split("/")[1]),
                                )
                                == trade["transaction_date"]
                                and _text(trade["transaction_type"])
                                in TRANSACTION_TYPES.get(p["tx_type"], ())
                            )
                        ][:30],
                        "same_ticker_pdf_rows": [
                            {"date": p["tx_date"], "type": p["tx_type"],
                             "amount": p["amount_str"]}
                            for p in same_ticker[:3]
                        ],
                        "same_asset_pdf_rows": [
                            {"date": p["tx_date"], "type": p["tx_type"],
                             "amount": p["amount_str"], "ticker": p["ticker"]}
                            for p in same_asset[:3]
                        ],
                        "closest_asset_pdf_rows": [
                            {"asset": _text(p["asset_name_raw"])[:120],
                             "date": p["tx_date"], "type": p["tx_type"],
                             "amount": p["amount_str"], "ticker": p["ticker"]}
                            for p in related_rows[:2]
                        ],
                    })
            continue
        parsed, pdf_match_count = supported
        suffix_match = _text(parsed["ticker"]) != _text(trade["ticker"])
        bucket = AMOUNT_BUCKET_IDS.get(_text(parsed["amount_str"]))
        if bucket is None or parsed["amount_min"] is None:
            counts["trade_amount_unparsed"] += 1
            continue
        bioguide, entity, identity_confidence, normalized_name, identity_evidence = (
            resolve_legislator(trade, "legislator_name", legislators)
        )
        archive_hash = archives[raw_id][1]
        evidence = json.dumps({
            "source": "archived House Clerk PDF",
            "raw_id": raw_id,
            "archive_sha256": archive_hash,
            "match": (
                "explicit reviewed PDF parser mismatch"
                if reviewed else
                "PDF asset suffix explains legacy ticker extraction"
                if suffix_match else
                "unique PDF date, transaction family, ticker, asset, and amount"
                if pdf_match_count == 1 else
                "duplicate PDF rows agree on amount and filing asset code"
            ),
            "matching_pdf_rows": pdf_match_count,
            "asset_type_code": _text(parsed["asset_type_code"]),
            "status": "candidate_requires_review",
            **identity_evidence,
        }, sort_keys=True)
        candidates.append([
            source_database, "alt_political_trades", _text(trade["trade_key"]),
            _text(trade["source_hash"]), bucket,
            parsed["amount_min"], parsed["amount_max"], "USD", "unresolved",
            normalized_name, bioguide, entity,
            None, None, None, None, identity_confidence, "unresolved",
            "", None, evidence, version, now,
        ])
        counts["trade_candidates"] += 1
        if identity_evidence:
            counts["trade_identity_alias_candidates"] += 1
        if pdf_match_count > 1:
            counts["trade_candidates_with_identical_pdf_duplicates"] += 1
        if suffix_match and not reviewed:
            counts["trade_candidates_with_asset_suffix_tickers"] += 1
        if reviewed:
            counts["trade_candidates_with_reviewed_parser_mismatches"] += 1
    return candidates, counts


def run(
    client: Any, source_database: str, *, stage: bool,
    review_examples: list[dict[str, Any]] | None = None,
) -> Counter[str]:
    filings, trades = _source_rows(client, source_database)
    archives = _archive_rows(client, source_database)
    # Join only approved legislator crosswalks to already-created canonical entities.
    legislators = {
        _text(bioguide): entity_id
        for bioguide, entity_id in client.query(
            "SELECT x.legacy_key, x.target_id FROM meta.migration_id_crosswalk AS x FINAL "
            "INNER JOIN ref.entities AS e FINAL ON x.target_id = e.entity_id "
            f"WHERE x.legacy_database = '{source_database}' "
            "AND x.legacy_table = 'alt_political_legislators' "
            "AND x.target_kind = 'entity' AND x.approved_at IS NOT NULL "
            "AND e.entity_type = 'person_legislator'"
        ).result_rows
    }
    now = datetime.now(UTC)
    candidates, counts = build_candidates(
        filings, trades, archives, legislators, source_database, now, time.time_ns(),
        review_examples,
    )
    existing = {
        (_text(table), _text(key), _text(source_hash)): (_text(bioguide), approved_at)
        for table, key, source_hash, bioguide, approved_at in client.query(
            "SELECT legacy_table, legacy_key, source_hash, bioguide_id, approved_at "
            "FROM meta.migration_political_trade_enrichment FINAL "
            f"WHERE legacy_database = '{source_database}'"
        ).result_rows
    }
    new_rows = []
    for row in candidates:
        prior = existing.get((row[1], row[2], row[3]))
        if prior is None:
            new_rows.append(row)
        elif not prior[0] and row[10] and prior[1] is None:
            new_rows.append(row)
            counts["refreshed_identity_candidates"] += 1
    counts["new_unapproved_candidates"] = len(new_rows)
    if stage:
        for offset in range(0, len(new_rows), 5000):
            client.insert(
                "meta.migration_political_trade_enrichment",
                new_rows[offset : offset + 5000], column_names=COLUMNS,
            )
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", default="factorlab")
    parser.add_argument("--stage", action="store_true", help="stage unapproved candidates")
    parser.add_argument("--yes", action="store_true", help="confirm staging mutation")
    parser.add_argument("--review-examples", action="store_true", help="print sample mismatches")
    args = parser.parse_args(argv)
    if args.stage and not args.yes:
        parser.error("--stage requires --yes")
    client = create_client()
    review_examples: list[dict[str, Any]] | None = [] if args.review_examples else None
    try:
        if args.stage:
            with migration_lock(client):
                counts = run(client, args.source_database, stage=True, review_examples=review_examples)
        else:
            counts = run(client, args.source_database, stage=False, review_examples=review_examples)
        print(json.dumps(counts, indent=2, sort_keys=True))
        if review_examples is not None:
            print(json.dumps(review_examples, indent=2, sort_keys=True))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
