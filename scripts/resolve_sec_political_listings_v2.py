"""Stage US-incorporated SEC listing candidates for the Wave 4 political migration.

Only an exact SEC ticker/exchange association, a supported issuer-name match,
and a confirming SEC submissions record can create canonical candidates.  The
source HTTP responses are archived.  Political correction updates remain
unapproved; this command never approves or runs a backfill.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import threading
import time
import urllib.request
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

try:
    from migrate_clickhouse_v2 import SAFE_IDENTIFIER_RE, create_client, migration_lock
except ImportError:
    from scripts.migrate_clickhouse_v2 import SAFE_IDENTIFIER_RE, create_client, migration_lock


MASTER_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
USER_AGENT = "FactorLab data migration (https://arjundoshi221.com/)"
ENTITY_NAMESPACE = uuid.UUID("65fce136-3b58-4149-958f-54c21ed4df7f")
SECURITY_NAMESPACE = uuid.UUID("c03aae2a-87bc-42da-b457-e6dd78f247af")
LISTING_NAMESPACE = uuid.UUID("d0ca3449-ef43-45a9-a716-30d92694ce31")
RAW_NAMESPACE = uuid.UUID("7be15277-6f9b-478f-a5a9-177fe829dd1d")
EXCHANGES = {"Nasdaq": ("NASDAQ", "XNAS"), "NYSE": ("NYSE", "XNYS"),
             "NYSE Arca": ("NYSE Arca", "ARCX")}
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
NAME_EXCEPTIONS = {"MRSH", "MMM"}  # Reviewed filing-name artifacts; still unapproved.
CORRECTION_COLUMNS = [
    "legacy_database", "legacy_table", "legacy_key", "source_hash", "amount_bucket_id",
    "amount_min", "amount_max", "amount_currency", "security_type",
    "normalized_legislator_name", "bioguide_id", "legislator_entity_id", "listing_id",
    "security_id", "entity_id", "contract_id", "bioguide_confidence",
    "asset_resolution_confidence", "approved_by", "approved_at", "evidence", "version",
    "ingested_at",
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    return str(value)


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def normalized_name(value: str) -> str:
    text = re.sub(r"/[A-Z]{2}/", " ", value.upper())
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    text = re.sub(
        r"\b(COMMON|STOCK|SHARES|SHARE|CLASS|INCORPORATED|INC|CORPORATION|CORP|"
        r"ORDINARY|PLC|LTD|LIMITED|CO|THE)\b", " ", text,
    )
    return " ".join(text.split())


def name_score(asset: str, issuer: str) -> float:
    return SequenceMatcher(None, normalized_name(asset), normalized_name(issuer)).ratio()


def canonical_ids(cik: int, ticker: str, exchange_code: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    base = f"sec:cik:{cik:010d}"
    return (
        uuid.uuid5(ENTITY_NAMESPACE, base),
        uuid.uuid5(SECURITY_NAMESPACE, f"{base}:ticker:{ticker}"),
        uuid.uuid5(LISTING_NAMESPACE, f"{base}:ticker:{ticker}:exchange:{exchange_code}"),
    )


def fetch(url: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f"SEC returned HTTP {response.status} for {url}")
        return response.read(), dict(response.headers.items())


def fetch_submissions(ciks: set[int]) -> dict[int, tuple[bytes, dict[str, str]]]:
    """Limit request starts to four per second, below SEC fair-access guidance."""
    lock = threading.Lock()
    next_start = [0.0]

    def one(cik: int) -> tuple[int, tuple[bytes, dict[str, str]] | None]:
        with lock:
            delay = max(0.0, next_start[0] - time.monotonic())
            if delay:
                time.sleep(delay)
            next_start[0] = time.monotonic() + 0.25
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        try:
            return cik, fetch(url)
        except (OSError, ValueError):
            return cik, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        return {cik: response for cik, response in pool.map(one, sorted(ciks)) if response}


def eligible_sec_listing(
    ticker: str, asset_names: list[str], master_rows: list[dict[str, Any]],
    submission: dict[str, Any] | None,
) -> tuple[dict[str, Any], float] | None:
    if len(master_rows) != 1 or submission is None:
        return None
    master = master_rows[0]
    if master["exchange"] not in EXCHANGES:
        return None
    score = max(name_score(name, master["name"]) for name in asset_names)
    if score < 0.35 and ticker not in NAME_EXCEPTIONS:
        return None
    if ticker not in submission.get("tickers", []):
        return None
    index = submission["tickers"].index(ticker)
    if submission.get("exchanges", [])[index] != master["exchange"]:
        return None
    incorporation = submission.get("stateOfIncorporation")
    if incorporation not in US_STATES and submission.get("stateOfIncorporationDescription") != "United States":
        return None
    if int(submission["cik"]) != int(master["cik"]):
        return None
    return master, score


def archive_row(
    url: str, body: bytes, headers: dict[str, str], now: datetime, request_key: str,
    channel: str,
) -> list[Any]:
    digest = hashlib.sha256(body).hexdigest()
    raw_id = uuid.uuid5(RAW_NAMESPACE, f"{url}:{digest}")
    return [
        raw_id, "sec_edgar", channel, "http", None, url, request_key, 200,
        json.dumps(headers, sort_keys=True), body.decode("utf-8"), "application/json", "",
        digest, now, None, None, now,
        json.dumps({"purpose": "ClickHouse v2 political listing resolver"}, sort_keys=True),
    ]


def _insert(client: Any, table: str, rows: list[list[Any]], columns: list[str]) -> int:
    for offset in range(0, len(rows), 500):
        client.insert(table, rows[offset : offset + 500], column_names=columns)
    return len(rows)


def run(client: Any, source_database: str, *, stage: bool) -> Counter[str]:
    if not SAFE_IDENTIFIER_RE.fullmatch(source_database):
        raise ValueError(f"unsafe source database identifier: {source_database!r}")
    trades = _rows(client.query(
        f"SELECT *, lower(hex(SHA256(toJSONString(tuple(*))))) AS source_hash "
        f"FROM {source_database}.alt_political_trades FINAL "
        "WHERE asset_type_code = 'ST' AND ticker IS NOT NULL AND ticker != ''"
    ))
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        by_ticker[_text(row["ticker"]).upper()].append(row)

    master_body, master_headers = fetch(MASTER_URL)
    master_json = json.loads(master_body)
    fields = master_json["fields"]
    master_tickers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for values in master_json["data"]:
        record = dict(zip(fields, values, strict=True))
        if record["ticker"].upper() in by_ticker:
            master_tickers[record["ticker"].upper()].append(record)
    possible_ciks = {
        int(records[0]["cik"])
        for ticker, records in master_tickers.items()
        if len(records) == 1 and records[0]["exchange"] in EXCHANGES
        and (max(name_score(_text(t["asset_name_raw"]), records[0]["name"])
                 for t in by_ticker[ticker]) >= 0.35 or ticker in NAME_EXCEPTIONS)
    }
    submissions = fetch_submissions(possible_ciks)
    parsed_submissions = {
        cik: json.loads(body) for cik, (body, _) in submissions.items()
    }
    eligible: dict[str, tuple[dict[str, Any], float]] = {}
    counts: Counter[str] = Counter()
    for ticker, source_rows in by_ticker.items():
        records = master_tickers.get(ticker, [])
        submission = parsed_submissions.get(int(records[0]["cik"])) if len(records) == 1 else None
        result = eligible_sec_listing(
            ticker, [_text(row["asset_name_raw"]) for row in source_rows], records,
            submission,
        )
        if result is None:
            counts["ineligible_ticker_rows"] += len(source_rows)
            continue
        eligible[ticker] = result
        for source in source_rows:
            if (name_score(_text(source["asset_name_raw"]), result[0]["name"]) >= 0.35
                    or ticker in NAME_EXCEPTIONS):
                counts["eligible_ticker_rows"] += 1
            else:
                counts["ineligible_ticker_rows"] += 1
    counts["eligible_tickers"] = len(eligible)
    if not stage:
        return counts

    now = datetime.now(UTC)
    version = time.time_ns()
    existing_raw = {
        uuid.UUID(_text(row[0])) for row in client.query(
            "SELECT raw_id FROM raw.archive WHERE source = 'sec_edgar'"
        ).result_rows
    }
    archive_rows = [archive_row(
        MASTER_URL, master_body, master_headers, now, "company_tickers_exchange",
        "company_tickers_exchange",
    )]
    for cik in sorted({int(item[0]["cik"]) for item in eligible.values()}):
        body, headers = submissions[cik]
        archive_rows.append(archive_row(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json", body, headers,
            now, f"CIK{cik:010d}", "submissions",
        ))
    counts["raw_archives"] = _insert(
        client, "raw.archive", [row for row in archive_rows if row[0] not in existing_raw],
        ["raw_id", "source", "source_channel", "transport", "country_code", "source_url",
         "request_key", "status_code", "response_headers", "response_body", "content_type",
         "content_encoding", "response_sha256", "fetched_at", "window_start_at",
         "event_count", "as_of_time", "metadata_json"],
    )
    master_raw_id = archive_rows[0][0]
    submission_raw_ids = {
        int(row[6][3:]): row[0] for row in archive_rows[1:]
    }

    existing_sources = {_text(row[0]) for row in client.query(
        "SELECT source_id FROM ref.sources FINAL"
    ).result_rows}
    if "sec_edgar" not in existing_sources:
        counts["sources"] = _insert(client, "ref.sources", [[
            "sec_edgar", "regulator", "sec", "none", ["US"], True,
            "SEC EDGAR public ticker and submissions JSON", version, now,
        ]], ["source_id", "kind", "vendor", "auth_type", "countries", "active",
             "notes", "version", "ingested_at"])

    existing_exchanges = {_text(row[0]) for row in client.query(
        "SELECT exchange_code FROM ref.exchanges FINAL"
    ).result_rows}
    if "NYSE" not in existing_exchanges and any(
        item[0]["exchange"] == "NYSE" for item in eligible.values()
    ):
        counts["exchanges"] = _insert(client, "ref.exchanges", [[
            "NYSE", "XNYS", "New York Stock Exchange", "US", "USD",
            "America/New_York", '{"regular":{"open":"09:30","close":"16:00"}}',
            True, version, now,
        ]], ["exchange_code", "mic", "name", "country_code", "currency_code",
             "timezone", "sessions", "active", "version", "ingested_at"])

    existing_listing_rows = _rows(client.query(
        "SELECT l.trading_symbol AS trading_symbol, l.listing_id AS listing_id, "
        "l.security_id AS security_id, s.entity_id AS entity_id, "
        "e.legal_name AS legal_name "
        "FROM ref.listings AS l FINAL "
        "INNER JOIN ref.securities AS s FINAL ON l.security_id = s.security_id "
        "INNER JOIN ref.entities AS e FINAL ON s.entity_id = e.entity_id "
        "WHERE l.country_code = 'US'"
    ))
    by_existing_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in existing_listing_rows:
        by_existing_symbol[_text(row["trading_symbol"]).upper()].append(row)
    existing_entities = {uuid.UUID(_text(row[0])) for row in client.query(
        "SELECT entity_id FROM ref.entities FINAL"
    ).result_rows}
    existing_securities = {uuid.UUID(_text(row[0])) for row in client.query(
        "SELECT security_id FROM ref.securities FINAL"
    ).result_rows}
    existing_listings = {uuid.UUID(_text(row[0])) for row in client.query(
        "SELECT listing_id FROM ref.listings FINAL"
    ).result_rows}
    entity_rows: list[list[Any]] = []
    security_rows: list[list[Any]] = []
    listing_rows: list[list[Any]] = []
    listing_map: dict[str, tuple[Any, Any, Any, int, float, str]] = {}
    for ticker, (sec, score) in eligible.items():
        cik = int(sec["cik"])
        exchange_code, mic = EXCHANGES[sec["exchange"]]
        current = by_existing_symbol.get(ticker, [])
        if len(current) > 1:
            counts["ambiguous_existing_listings"] += 1
            continue
        if current:
            if name_score(_text(current[0]["legal_name"]), sec["name"]) < 0.35:
                counts["conflicting_existing_listings"] += 1
                continue
            entity, security, listing = (
                current[0]["entity_id"], current[0]["security_id"], current[0]["listing_id"]
            )
        else:
            entity, security, listing = canonical_ids(cik, ticker, exchange_code)
            if entity not in existing_entities:
                entity_rows.append([
                    entity, "issuer", sec["name"], None, "US", "US", True,
                    now.date(), now.date(), version, now,
                ])
                existing_entities.add(entity)
            if security not in existing_securities:
                security_rows.append([
                    security, entity, "common", None, None, None, "", "USD",
                    None, None, None, "", True, version, now,
                ])
                existing_securities.add(security)
            if listing not in existing_listings:
                listing_rows.append([
                    listing, security, exchange_code, "US", ticker, None, mic, 1, None,
                    True, None, None, True, version, now,
                ])
                existing_listings.add(listing)
        listing_map[ticker] = (listing, security, entity, cik, score, exchange_code)
    counts["issuer_entities"] = _insert(client, "ref.entities", entity_rows,
        ["entity_id", "entity_type", "legal_name", "lei", "country_of_domicile",
         "country_of_incorp", "active", "first_seen", "last_seen", "version", "ingested_at"])
    counts["securities"] = _insert(client, "ref.securities", security_rows,
        ["security_id", "entity_id", "security_type", "isin", "cusip", "figi",
         "share_class", "currency_code", "issue_date", "maturity_date", "sector_id",
         "sector_classification", "active", "version", "ingested_at"])
    counts["listings"] = _insert(client, "ref.listings", listing_rows,
        ["listing_id", "security_id", "exchange_code", "country_code", "trading_symbol",
         "local_symbol", "mic", "lot_size", "tick_size", "is_primary", "first_traded",
         "last_traded", "active", "version", "ingested_at"])

    existing_cik_aliases = {
        (_text(row[0]), uuid.UUID(_text(row[1]))) for row in client.query(
            "SELECT alias_value, target_id FROM ref.identifier_aliases FINAL "
            "WHERE alias_kind = 'cik' AND target_kind = 'entity'"
        ).result_rows
    }
    alias_rows = []
    for ticker, (_, _, entity, cik, score, _) in listing_map.items():
        marker = (str(cik), uuid.UUID(_text(entity)))
        if marker in existing_cik_aliases:
            continue
        alias_rows.append([
            "cik", str(cik), None, None, "entity", entity, "sec_edgar", now.date(),
            None, "high" if score >= 0.35 else "medium",
            json.dumps({"master_raw_id": str(master_raw_id),
                        "submission_raw_id": str(submission_raw_ids[cik])}, sort_keys=True),
            version, now,
        ])
        existing_cik_aliases.add(marker)
    counts["cik_aliases"] = _insert(client, "ref.identifier_aliases", alias_rows,
        ["alias_kind", "alias_value", "scope_country", "scope_exchange", "target_kind",
         "target_id", "source", "valid_from", "valid_to", "confidence", "notes",
         "version", "ingested_at"])

    corrections = {
        (_text(row["legacy_key"]), _text(row["source_hash"])): row
        for row in _rows(client.query(
            "SELECT * FROM meta.migration_political_trade_enrichment FINAL "
            f"WHERE legacy_database = '{source_database}' AND legacy_table = 'alt_political_trades'"
        ))
    }
    correction_rows = []
    for ticker, source_rows in by_ticker.items():
        target = listing_map.get(ticker)
        if target is None:
            continue
        listing, security, entity, cik, _score, exchange_code = target
        for source in source_rows:
            row_score = name_score(
                _text(source["asset_name_raw"]), eligible[ticker][0]["name"]
            )
            if row_score < 0.35 and ticker not in NAME_EXCEPTIONS:
                counts["skipped_low_name_trade_rows"] += 1
                continue
            correction = corrections.get((_text(source["trade_key"]), _text(source["source_hash"])))
            if correction is None or correction["approved_at"] is not None:
                continue
            if correction["listing_id"] is not None:
                if correction["listing_id"] != listing:
                    raise ValueError(f"conflicting listing for trade {_text(source['trade_key'])}")
                continue
            updated = dict(correction)
            updated.update({
                "security_type": "common", "listing_id": listing, "security_id": security,
                "entity_id": entity,
                "asset_resolution_confidence": "high" if row_score >= 0.35 else "medium",
                "version": version, "ingested_at": now,
            })
            evidence = json.loads(_text(correction["evidence"]))
            evidence["sec_listing"] = {
                "cik": cik, "ticker": ticker, "exchange": exchange_code,
                "issuer_name_score": round(row_score, 3),
                "master_raw_id": str(master_raw_id),
                "submission_raw_id": str(submission_raw_ids[cik]),
                "status": "candidate_requires_review",
            }
            updated["evidence"] = json.dumps(evidence, sort_keys=True)
            correction_rows.append([updated[column] for column in CORRECTION_COLUMNS])
    counts["enriched_unapproved_trades"] = _insert(
        client, "meta.migration_political_trade_enrichment", correction_rows,
        CORRECTION_COLUMNS,
    )
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", default="factorlab")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if args.stage and not args.yes:
        parser.error("--stage requires --yes")
    client = create_client()
    try:
        if args.stage:
            with migration_lock(client):
                result = run(client, args.source_database, stage=True)
        else:
            result = run(client, args.source_database, stage=False)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
