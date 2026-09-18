"""Walk existing data/political/raw/senate_efd/ptrs/*paper*.json manifests and
write placeholder rows to alt_political_us.legislator_trades.

Use after a Senate eFD scrape that pre-dates the placeholder logic in
senate_efd/ingest.py — back-fills the audit record so we know which paper
PTRs are sitting on disk waiting for OCR.

Idempotent — uses fast_upsert with the standard UNIQUE conflict keys.

Run:
    python scripts/backfill_paper_ptr_placeholders.py
    python scripts/backfill_paper_ptr_placeholders.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._db import fast_upsert
from factorlab.countries.us.political.senate_efd.ingest import _paper_placeholder_row
from factorlab.storage.db import get_engine
from factorlab.storage.schemas.alt_political_us import legislator_trades

PROJECT_ROOT = Path(__file__).resolve().parents[4]

PTR_DIR = PROJECT_ROOT / "data" / "political" / "raw" / "senate_efd" / "ptrs"

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"backfill_paper_ptr_placeholders_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("backfill_paper_ptr_placeholders")


def _slug_to_field(slug_part: str) -> str:
    """Filename slugs use '_' as separator; reverse into a name."""
    return slug_part.replace("_", " ").strip()


def _parse_filename(p: Path) -> dict | None:
    """Filename pattern: MM-DD-YYYY_LAST_FIRST_uuid_paper.html|json
    e.g. 02-01-2021_BLUMENTHAL_RICHARD_43c7..._paper.json
    """
    name = p.stem
    if not name.endswith("_paper"):
        return None
    name = name[:-len("_paper")]
    # Last 5 underscored chunks form the UUID; date is first chunk; rest = name
    parts = name.split("_")
    if len(parts) < 7:  # date + last + first + 5 uuid chunks
        return None
    date_str = parts[0]  # MM-DD-YYYY
    uuid_chunks = parts[-5:]
    name_parts = parts[1:-5]
    if len(name_parts) < 2:
        return None
    last = name_parts[0]
    first = " ".join(name_parts[1:])
    uuid = "-".join(uuid_chunks)
    return {
        "filed_date": date_str.replace("-", "/"),  # MM/DD/YYYY for matcher
        "last": last,
        "first": first,
        "uuid": uuid,
    }


def main(*, apply: bool) -> None:
    log.info("logging to %s", log_file)
    if not PTR_DIR.exists():
        log.error("Directory not found: %s", PTR_DIR)
        return
    json_files = sorted(PTR_DIR.glob("*_paper.json"))
    log.info("Found %d paper-PTR JSON manifests (half-baked trades, OCR pending)", len(json_files))

    engine = get_engine()
    matcher = StrictBioguideMatcher(engine)

    rows: list[dict] = []
    stats: Counter = Counter()
    for jf in json_files:
        meta = _parse_filename(jf)
        if not meta:
            log.warning("could not parse filename: %s", jf.name)
            stats["unparseable_filename"] += 1
            continue
        try:
            sidecar = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("could not read JSON %s: %s", jf.name, e)
            stats["read_error"] += 1
            continue
        # Reconstruct the manifest dict the ingest helper expects
        manifest = {
            "first": meta["first"],
            "last": meta["last"],
            "office": f"{meta['last'].title()}, {meta['first'].title()} (Senator)",
            "filed_date": meta["filed_date"],
            "link": sidecar.get("filing_url"),
            "page_count": sidecar.get("page_count"),
        }
        row = _paper_placeholder_row(manifest, matcher)
        if not row:
            stats["could_not_build_row"] += 1
            log.warning("[PAPER] SKIPPED %s %s %s | could not build row",
                        meta["filed_date"], meta["last"], meta["first"])
            continue
        rows.append(row)
        resolved = bool(row["bioguide_id"])
        stats["resolved" if resolved else "unresolved"] += 1
        log.info("[PAPER] %-10s %s %s %s | filing_id=%s pages=%s bioguide=%s",
                 "RESOLVED" if resolved else "UNRESOLVED",
                 meta["filed_date"], meta["last"], meta["first"],
                 row["filing_id"], manifest.get("page_count", "?"),
                 row["bioguide_id"] or "-")

    log.info("Stats: %s", dict(stats))
    log.info("Built %d placeholder rows", len(rows))

    # Dedup defensively (same conflict keys as the upsert)
    seen: dict[tuple, dict] = {}
    for r in rows:
        k = (r["source"], r["country_code"], r["chamber"], r["filing_id"],
             r["transaction_date"], r["asset_name_raw"], r["transaction_type"],
             r["amount_str"])
        seen[k] = r
    unique = list(seen.values())
    log.info("After dedup: %d unique rows", len(unique))

    # Show breakdown by senator
    by_filer: Counter = Counter()
    for r in unique:
        by_filer[r["legislator_name_raw"]] += 1
    log.info("Per-filer paper-PTR counts:")
    for filer, n in by_filer.most_common():
        log.info("  %-50s  n=%d", filer, n)

    if not apply:
        log.info("(dry-run — no DB writes; pass --apply to insert)")
        return

    if not unique:
        return

    fast_upsert(
        engine, legislator_trades, unique,
        conflict_keys=["source", "country_code", "chamber", "filing_id",
                       "transaction_date", "asset_name_raw", "transaction_type",
                       "amount_str"],
        update_cols=["bioguide_id", "legislator_name_raw", "filing_url",
                     "filer_type", "asset_type_code", "as_of_time"],
    )
    log.info("Inserted/updated %d paper-PTR placeholder rows", len(unique))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = ap.parse_args()
    main(apply=not args.dry_run)
