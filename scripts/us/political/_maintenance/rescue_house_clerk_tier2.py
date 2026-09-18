"""Rescue tier-2 rows that the automated quality gates rejected but Claude-
verified are real trades. Two specific patterns:

1. **bg_unresolved** — manually-mapped name resolves via the
   `alt_political_us.legislator_aliases` table (Van Taylor → T000479,
   confirmed via 2022_20020767.pdf — seeded by migration 024).

2. **header_pollution** / **asset_too_short** where ticker is canonical (BLK,
   RH, IXNZF, ABT, etc.) — trade is real, asset_name was contaminated by the
   filer's free-text comment/description field. Replace asset_name with the
   ticker string when polluted (no fabricated canonical names — strict-NULL).

All rescued rows pass through the same fast_upsert. Idempotent.

Run:
    python scripts/rescue_house_clerk_tier2.py --tier2-csv logs/.../tier2_review.csv
    python scripts/rescue_house_clerk_tier2.py --tier2-csv ... --apply
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import text

from factorlab.countries.us.political._db import fast_upsert
from factorlab.storage.db import get_engine
from factorlab.storage.schemas.alt_political_us import legislator_trades


def _norm_alias(s: str) -> str:
    """Normalize a name for alias-table lookup: lowercase, collapse whitespace.

    Must match the canonicalization used when seeding the legislator_aliases
    table (migration 024: lowercase, single-spaced). Punctuation kept as-is
    so 'Nicholas V. Taylor' -> 'nicholas v. taylor' (matches seed exactly).
    """
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _override_bioguide(engine, name: str) -> str | None:
    """Look up `name` in alt_political_us.legislator_aliases (Stage 0 of the
    matcher). Returns bioguide_id or None if no override exists."""
    if not name:
        return None
    norm = _norm_alias(name)
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT bioguide_id FROM alt_political_us.legislator_aliases
            WHERE country_code = 'US' AND alias_normalized = :a
            LIMIT 1
        """), {"a": norm}).fetchone()
    return row[0] if row else None


def _clean_asset_name(name: str, ticker: str | None) -> str:
    """If asset_name contains free-text comment markers, fall back to ticker.

    Strict-NULL policy: no fabricated canonical names from the classifier —
    we don't claim a name we didn't see in the source. The ticker is the
    cleanest identifier when the upstream name is contaminated.
    """
    polluted_markers = (
        "broker", "purchased shares of", "sold partial", "discovered",
        "death", "estate", "manager", "transactions", "report preparer",
        "completion of probate", "administrative error",
    )
    is_polluted = any(m in name.lower() for m in polluted_markers)
    short = len(name.strip()) <= 3
    if (is_polluted or short) and ticker:
        return ticker
    return name


def main(*, tier2_csv: Path, apply: bool) -> int:
    LOG_DIR = PROJECT_ROOT / "logs"
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"rescue_house_clerk_tier2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("rescue_t2")
    log.info("logging to %s", log_file)
    log.info("tier2_csv = %s  apply = %s", tier2_csv, apply)

    engine = get_engine()
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "house_clerk_ptr")

    with open(tier2_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    log.info("loaded %d tier-2 rows", len(rows))

    rescued: list[dict] = []
    skipped: list[tuple[dict, str]] = []
    stats: Counter = Counter()

    for r in rows:
        reasons = (r.get("_failure_reasons") or "").split("|")

        # Resolve bioguide
        bioguide = (r.get("bioguide_id") or "").strip() or None
        if not bioguide:
            bioguide = _override_bioguide(engine, r["legislator_name_raw"])
            if bioguide:
                stats["bioguide_overridden"] += 1
                log.info("[bg-override] %s -> %s",
                         r["legislator_name_raw"], bioguide)

        # If still no bioguide, skip (per strict policy: no wrong > no entry)
        if not bioguide:
            skipped.append((r, "no_bioguide"))
            stats["skipped_no_bioguide"] += 1
            continue

        # Validate ticker
        ticker = (r.get("ticker") or "").strip().upper() or None
        if not ticker:
            # Without a canonical ticker, we can't safely rescue header-polluted rows
            skipped.append((r, "no_ticker"))
            stats["skipped_no_ticker"] += 1
            continue

        # Clean asset_name
        cleaned_asset = _clean_asset_name(r["asset_name_raw"], ticker)

        # Build the rescue row
        rescue_row = {
            "country_code": "US",
            "chamber": "house",
            "bioguide_id": bioguide,
            "legislator_name_raw": r["legislator_name_raw"][:200],
            "endpoint_id": ENDPOINT_ID,
            "filing_id": r["filing_id"][:50],
            "filing_date": r["filing_date"] or None,
            "filing_url": r.get("filing_url", "")[:500] or None,
            "transaction_date": r["transaction_date"] or None,
            "notification_date": None,
            "filer_type": r.get("filer_type") or "self",
            "transaction_type": r["transaction_type"] or "purchase",
            "asset_name_raw": cleaned_asset[:5000],
            "asset_type_code": (r.get("asset_type_code") or "").strip() or None,
            "ticker": ticker,
            "amount_str": r["amount_str"][:40],
            "amount_min": None,
            "amount_max": None,
            "amount_mid": None,
            "raw_archive_id": None,
        }
        rescued.append(rescue_row)
        stats["rescued"] += 1

    log.info("=" * 60)
    log.info("RESCUE SUMMARY")
    log.info("=" * 60)
    for k, v in stats.most_common():
        log.info("  %-30s %d", k, v)
    log.info("rescued total: %d", len(rescued))
    log.info("skipped total: %d", len(skipped))

    if skipped:
        log.info("\nSkipped rows:")
        for r, why in skipped[:20]:
            log.info("  [%s] %s  ticker=%s  filing_id=%s",
                     why, r["legislator_name_raw"], r.get("ticker"), r.get("filing_id"))

    if not apply:
        log.info("\n(DRY-RUN — pass --apply to upsert)")
        return 0

    if not rescued:
        log.info("nothing to insert")
        return 0

    # Dedup
    seen: dict[tuple, dict] = {}
    for row in rescued:
        k = (row["source"], row["country_code"], row["chamber"], row["filing_id"],
             row["transaction_date"], row["asset_name_raw"], row["transaction_type"], row["amount_str"])
        seen[k] = row
    unique = list(seen.values())
    log.info("after dedup: %d unique", len(unique))

    fast_upsert(
        engine, legislator_trades, unique,
        conflict_keys=["source", "country_code", "chamber", "filing_id",
                       "transaction_date", "asset_name_raw", "transaction_type", "amount_str"],
        update_cols=["bioguide_id", "legislator_name_raw", "filing_url",
                     "filer_type", "asset_type_code", "ticker", "as_of_time"],
    )
    log.info("upserted %d rescued rows", len(unique))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier2-csv", type=Path, required=True,
                    help="Path to a tier2_review.csv from a load run")
    ap.add_argument("--apply", action="store_true",
                    help="Actually insert (default: dry-run)")
    args = ap.parse_args()
    sys.exit(main(tier2_csv=args.tier2_csv, apply=args.apply))
