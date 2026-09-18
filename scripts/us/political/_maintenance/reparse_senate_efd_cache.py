"""Re-parse cached senate_efd HTMLs and fix NULL transaction_type rows.

Background: the senate_efd parser had a header-lookup bug where `col("type")`
matched "Asset Type" instead of "Type" via substring. This caused
~30-40% of recent filings (2024-2026) to land with NULL transaction_type
in the DB. Fix shipped in parser.py (exact-match-first); this script
rebuilds the affected rows from the local HTML cache (no Playwright /
Akamai re-scrape needed).

Strategy:
  1. Find filings whose DB rows have ANY NULL transaction_type.
  2. For each such filing, locate the cached HTML (matched by filing_id
     embedded in the filename).
  3. DELETE all DB rows for that filing (so the corrected upsert isn't
     blocked by the NULL-versus-value mismatch in the unique constraint).
  4. Re-parse the HTML with the fixed parser; build trade rows.
  5. UPSERT — should now land with proper purchase/sale_full/sale_partial.

Idempotent. Safe to re-run. Only touches senate_efd_ptr rows.

Run:
  python scripts/_reparse_senate_efd_cache.py --dry-run        # see scope
  python scripts/_reparse_senate_efd_cache.py                  # actually fix
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from factorlab.shared.paths import raw_dir
from factorlab.countries.us.political._asset_classifier import AssetClassifier
from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._db import fast_upsert, lookup_endpoint_id
from factorlab.countries.us.political.senate_efd.parser import parse_html
from factorlab.storage.db import get_engine
from factorlab.storage.schemas.alt_political_us import legislator_trades

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

CACHE_DIR = raw_dir("senate_efd_ptrs")


def _filing_id_from_filename(p: Path) -> str | None:
    """Extract the UUID from the cached filename. Reverse of the scraper's
    slug logic: filenames look like
        02-08-2025_Boozman_John_16381620_d336_4b9a_98fd_86dd3364c8c5.html
    UUIDs in filenames have underscores instead of dashes.
    """
    stem = p.stem.removesuffix("_paper")
    # Last 5 underscore-separated chunks form the UUID
    parts = stem.split("_")
    if len(parts) < 5:
        return None
    uuid_parts = parts[-5:]
    candidate = "-".join(uuid_parts)
    # Loose UUID-shape sanity check
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                candidate, re.I):
        return candidate
    return None


def _parse_us_or_iso_date(s: str):
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report scope and don't write")
    args = ap.parse_args()

    engine = get_engine()
    endpoint_id = lookup_endpoint_id(engine, "senate_efd_ptr")
    matcher = StrictBioguideMatcher(engine)
    classifier = AssetClassifier()

    # 1. Find filings that have NULL transaction_type rows
    with engine.connect() as c:
        bad = c.execute(text("""
            SELECT DISTINCT t.filing_id
              FROM alt_political_us.legislator_trades t
              JOIN ref.data_endpoints e ON e.id = t.endpoint_id
             WHERE e.code = 'senate_efd_ptr'
               AND t.transaction_type IS NULL
               AND t.asset_name_raw NOT LIKE '[PAPER PTR%'
        """)).all()
    bad_filings = {r[0] for r in bad}
    log.info("Found %d filings with NULL tx_type rows (HTML, not paper)",
             len(bad_filings))

    # 2. Index local HTML cache by filing_id
    cache_index: dict[str, Path] = {}
    for p in CACHE_DIR.glob("*.html"):
        if "_paper" in p.stem:
            continue
        fid = _filing_id_from_filename(p)
        if fid:
            cache_index[fid] = p
    log.info("Indexed %d HTML files in cache", len(cache_index))

    # 3. Match
    matched = bad_filings & cache_index.keys()
    missing = bad_filings - cache_index.keys()
    log.info("Of %d bad filings: %d have cached HTML, %d are missing",
             len(bad_filings), len(matched), len(missing))
    if missing and len(missing) <= 5:
        for fid in list(missing)[:5]:
            log.info("  missing cache for filing_id=%s", fid)

    if not matched:
        log.info("Nothing to do.")
        return 0

    if args.dry_run:
        # Sample-parse 3 to show what the fix would produce
        log.info("DRY RUN — sample-parsing 3 filings:")
        for fid in list(matched)[:3]:
            html_path = cache_index[fid]
            trades = parse_html(html_path)
            n_known = sum(1 for t in trades if t.get("tx_type"))
            log.info("  %s  trades=%d  known_tx_types=%d  file=%s",
                     fid, len(trades), n_known, html_path.name)
        log.info("(re-run without --dry-run to apply)")
        return 0

    # 4. For each matched filing: DELETE existing rows, re-parse, upsert
    fixed = 0
    skipped = 0
    rows_deleted = 0
    rows_inserted = 0

    for fid in sorted(matched):
        html_path = cache_index[fid]
        try:
            parsed_trades = parse_html(html_path)
        except Exception as e:
            log.warning("parse fail %s: %s", html_path.name, e)
            skipped += 1
            continue
        if not parsed_trades:
            skipped += 1
            continue

        # Pull the pre-existing DB rows for context (filing_date, names) so
        # we don't have to re-derive from the HTML.
        with engine.connect() as c:
            ctx = c.execute(text("""
                SELECT bioguide_id, legislator_name_raw, filing_url, filing_date,
                       country_code
                  FROM alt_political_us.legislator_trades
                 WHERE endpoint_id = :eid AND filing_id = :fid
                 LIMIT 1
            """), {"eid": endpoint_id, "fid": fid}).first()
        if ctx is None:
            skipped += 1
            continue
        bioguide, lname, furl, fdate, ccode = ctx

        out_rows: list[dict] = []
        for t in parsed_trades:
            tx_date = _parse_us_or_iso_date(t["tx_date_str"])
            if not tx_date:
                continue
            ticker = t.get("ticker")
            if ticker and (len(ticker) > 10 or " " in ticker):
                ticker = None
            asset_name_raw = t.get("asset_name_raw") or ""
            cls = classifier.classify(asset_name_raw)
            asset_type_code = cls.asset_type_code
            if not ticker and cls.ticker:
                ticker = cls.ticker
            out_rows.append({
                "country_code": ccode,
                "chamber": "senate",
                "bioguide_id": bioguide,
                "legislator_name_raw": lname,
                "endpoint_id": endpoint_id,
                "filing_id": fid[:50],
                "filing_date": fdate,
                "filing_url": furl,
                "transaction_date": tx_date,
                "notification_date": None,
                "filer_type": t.get("owner") or None,
                "transaction_type": t.get("tx_type"),
                "asset_name_raw": (asset_name_raw or "")[:5000],
                "asset_type_code": asset_type_code,
                "ticker": ticker,
                "amount_str": (t.get("amount_str") or "")[:40],
                "amount_min": t.get("amount_min"),
                "amount_max": t.get("amount_max"),
                "amount_mid": t.get("amount_mid"),
                "raw_archive_id": None,
            })

        if not out_rows:
            skipped += 1
            continue

        # Atomic swap: delete then insert in a single transaction
        with engine.begin() as conn:
            d = conn.execute(text("""
                DELETE FROM alt_political_us.legislator_trades
                 WHERE endpoint_id = :eid AND filing_id = :fid
            """), {"eid": endpoint_id, "fid": fid})
            rows_deleted += d.rowcount or 0

        # Dedup within batch (some PTRs list duplicates)
        seen = set()
        unique = []
        for r in out_rows:
            k = (r["endpoint_id"], r["country_code"], r["chamber"], r["filing_id"],
                 r["transaction_date"], r["asset_name_raw"], r["transaction_type"],
                 r["amount_str"])
            if k not in seen:
                seen.add(k)
                unique.append(r)
        fast_upsert(
            engine, legislator_trades, unique,
            conflict_keys=["endpoint_id", "country_code", "chamber", "filing_id",
                           "transaction_date", "asset_name_raw",
                           "transaction_type", "amount_str"],
            update_cols=["bioguide_id", "legislator_name_raw", "filing_url",
                         "filer_type", "ticker", "asset_type_code",
                         "amount_min", "amount_max", "amount_mid", "as_of_time"],
        )
        rows_inserted += len(unique)
        fixed += 1
        if fixed % 25 == 0:
            log.info("  progress: fixed %d/%d filings", fixed, len(matched))

    log.info("DONE. filings_fixed=%d  skipped=%d  rows_deleted=%d  rows_inserted=%d",
             fixed, skipped, rows_deleted, rows_inserted)

    # 5. Verify
    with engine.connect() as c:
        remaining = c.execute(text("""
            SELECT COUNT(*) FROM alt_political_us.legislator_trades t
              JOIN ref.data_endpoints e ON e.id = t.endpoint_id
             WHERE e.code = 'senate_efd_ptr'
               AND t.transaction_type IS NULL
               AND t.asset_name_raw NOT LIKE '[PAPER PTR%'
        """)).scalar()
    log.info("Remaining NULL tx_type rows (non-paper): %d", remaining)
    return 0


if __name__ == "__main__":
    sys.exit(main())
