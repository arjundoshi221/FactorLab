"""Insert vision-LLM-extracted Senate eFD paper-PTR trades.

Reads JSON files from `data/political/raw/senate_efd/_llm_extractions/`
(one per filing, named `<filing_id>.json`), runs each row through
ticker resolution + bioguide matching, and upserts into
`alt_political_us.legislator_trades` under the
`senate_efd_paper_llm` endpoint code (provenance tag).

JSON schema (one file per filing):
    {
      "filing_id":   "<uuid>",
      "filing_date": "YYYY-MM-DD",
      "filing_url":  "https://efdsearch.senate.gov/search/view/paper/<uuid>/",
      "senator":     "John Boozman",
      "bioguide_id": "B001236",
      "extracted_by": "claude_opus_vision",
      "extracted_at": "YYYY-MM-DD",
      "source_pages": ["page_002.gif", ...],
      "title_text": "...",
      "trades": [
        {
          "section": "sales|purchases|exchanges",
          "asset_name_raw": "...",
          "tx_date": "YYYY-MM-DD",
          "amount_str": "$1,001 - $15,000",
          "amount_min": 1001,
          "amount_max": 15000,
          "amount_mid": 8000,
          "ticker_guess": "AAPL"  // or null
        },
        ...
      ]
    }

Idempotent — re-running is safe (fast_upsert on the standard conflict key).

Usage:
    python scripts/_insert_senate_efd_llm_extractions.py            # dry-run
    python scripts/_insert_senate_efd_llm_extractions.py --apply    # write
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from factorlab.shared.paths import raw_dir
from factorlab.countries.us.political._asset_classifier import AssetClassifier
from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._db import fast_upsert, lookup_endpoint_id
from factorlab.storage.db import get_engine
from factorlab.storage.schemas.alt_political_us import legislator_trades

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

EXTRACTION_DIR = raw_dir("senate_efd_llm")

SECTION_TO_TX_TYPE = {
    "sales": None,            # Section-only — actual purchase/sale_full/sale_partial
                              # would need granular detection. Section "sales"
                              # implies it's a sale; we map to 'sale_full' as
                              # a reasonable default since paper attachment
                              # format doesn't break out partial vs full.
    "purchases": "purchase",
    "exchanges": "exchange",
}
# More accurate: section "sales" → sale_full (one section, one bucket).
SECTION_TO_TX_TYPE_RESOLVED = {
    "sales": "sale_full",
    "purchases": "purchase",
    "exchanges": "exchange",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually upsert (default: dry-run)")
    ap.add_argument("--filing-id", default=None,
                    help="restrict to a single filing_id (for spot-checking)")
    args = ap.parse_args()

    if not EXTRACTION_DIR.exists():
        log.error("extraction dir missing: %s", EXTRACTION_DIR)
        return 2

    engine = get_engine()
    endpoint_id = lookup_endpoint_id(engine, "senate_efd_paper_llm")
    if endpoint_id is None:
        log.error("ref.data_endpoints has no row for code='senate_efd_paper_llm' — "
                  "add it via SQL first")
        return 2
    log.info("endpoint_id=%d (senate_efd_paper_llm)", endpoint_id)

    matcher = StrictBioguideMatcher(engine)
    classifier = AssetClassifier()

    json_files = sorted(EXTRACTION_DIR.glob("*.json"))
    if args.filing_id:
        json_files = [p for p in json_files if args.filing_id in p.stem]
    log.info("Found %d extraction file(s)", len(json_files))

    all_rows: list[dict] = []
    per_file_summary: list[tuple[str, int, int, int]] = []

    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("skip unreadable %s: %s", jf.name, e)
            continue

        filing_id = data["filing_id"]
        filing_url = data.get("filing_url") or ""
        filing_date = datetime.strptime(data["filing_date"], "%Y-%m-%d").date()
        senator_name = data.get("senator") or ""
        # Trust the extraction's bioguide if present, else look up
        bioguide = data.get("bioguide_id")
        if not bioguide and senator_name:
            # Use first trade's date for term-overlap, else filing_date
            first_tx = next(
                (datetime.strptime(t["tx_date"], "%Y-%m-%d").date()
                 for t in data.get("trades") or [] if t.get("tx_date")),
                filing_date,
            )
            parts = senator_name.split()
            first = parts[0] if parts else None
            last = parts[-1] if parts else None
            bioguide = matcher.resolve(first=first, last=last,
                                       chamber="sen", trade_date=first_tx)

        rows_in: list[dict] = []
        ticker_resolved = 0
        for t in data.get("trades") or []:
            tx_date = datetime.strptime(t["tx_date"], "%Y-%m-%d").date()
            section = t.get("section") or ""
            tx_type = SECTION_TO_TX_TYPE_RESOLVED.get(section)
            if tx_type is None:
                # Drop rows we can't categorize — strict-NULL would still leave
                # them, but the unique constraint includes tx_type and we want
                # clean upserts. Log + skip.
                log.warning("[%s] skipping row — unknown section %r",
                            filing_id, section)
                continue

            asset_name_raw = (t.get("asset_name_raw") or "").strip()
            # Resolve ticker: prefer the LLM's guess, fall back to classifier
            ticker = t.get("ticker_guess")
            if ticker:
                ticker = ticker.strip().upper()
                if not ticker or len(ticker) > 10 or " " in ticker:
                    ticker = None
            asset_type_code = None
            cls = classifier.classify(asset_name_raw)
            asset_type_code = cls.asset_type_code
            if not ticker and cls.ticker:
                ticker = cls.ticker
            if ticker:
                ticker_resolved += 1

            rows_in.append({
                "country_code": "US",
                "chamber": "senate",
                "bioguide_id": bioguide,
                "legislator_name_raw": (senator_name or "")[:200],
                "endpoint_id": endpoint_id,
                "filing_id": filing_id[:50],
                "filing_date": filing_date,
                "filing_url": filing_url[:500] or None,
                "transaction_date": tx_date,
                "notification_date": None,
                "filer_type": "self",
                "transaction_type": tx_type,
                "asset_name_raw": asset_name_raw[:5000],
                "asset_type_code": asset_type_code,
                "ticker": ticker,
                "amount_str": (t.get("amount_str") or "")[:40],
                "amount_min": t.get("amount_min"),
                "amount_max": t.get("amount_max"),
                "amount_mid": t.get("amount_mid"),
                "raw_archive_id": None,
            })

        per_file_summary.append((filing_id[:8], len(data.get("trades") or []),
                                  len(rows_in), ticker_resolved))
        all_rows.extend(rows_in)

    log.info("")
    log.info(f"{'filing':<10} {'parsed':>7} {'inserts':>8} {'tickers':>8}")
    for fid, parsed, ins, tk in per_file_summary:
        log.info(f"{fid:<10} {parsed:>7} {ins:>8} {tk:>8}")
    log.info("")
    log.info("TOTAL prepared rows: %d", len(all_rows))

    if not all_rows:
        return 0

    if not args.apply:
        log.info("DRY RUN — re-run with --apply to insert")
        return 0

    # Dedup within batch on the conflict key
    seen = set()
    unique = []
    for r in all_rows:
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
    log.info("Upserted %d unique rows", len(unique))

    # Verify
    with engine.connect() as c:
        n = c.execute(text("""
            SELECT COUNT(*) FROM alt_political_us.legislator_trades t
              JOIN ref.data_endpoints e ON e.id = t.endpoint_id
             WHERE e.code = 'senate_efd_paper_llm'
        """)).scalar()
    log.info("DB now has %d senate_efd_paper_llm rows", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
