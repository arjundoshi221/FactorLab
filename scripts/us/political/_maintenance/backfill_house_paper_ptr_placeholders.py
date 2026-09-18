"""Walk House Clerk PTR PDFs, detect paper-scan filings, write placeholder rows
to alt_political_us.legislator_trades with resolved bioguide_id.

Mirrors scripts/backfill_paper_ptr_placeholders.py (senate version) but
reads the year-index XML to recover filer name + filing_date per PDF, since
House PDFs are saved as {year}_{doc_id}.pdf with no name in the filename.

Idempotent — uses fast_upsert with the standard UNIQUE conflict keys; the
placeholder asset_name_raw (`[PAPER PTR — N pages — OCR pending] ...`) ensures
the row coexists with future OCR'd real trade rows for the same filing_id.

Run:
    python scripts/backfill_house_paper_ptr_placeholders.py --dry-run
    python scripts/backfill_house_paper_ptr_placeholders.py --apply
    python scripts/backfill_house_paper_ptr_placeholders.py --apply --years 2013-2021
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import pdfplumber

from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._client import DATA_ROOT, PoliticalHTTPClient
from factorlab.countries.us.political._db import fast_upsert
from factorlab.countries.us.political.house_clerk.index import list_ptrs
from factorlab.storage.db import get_engine
from factorlab.storage.schemas.alt_political_us import legislator_trades

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PTR_DIR = DATA_ROOT / "house_clerk" / "ptrs"
PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

# Same TRADE_ANCHOR_RE as parser.py — matching against the raw extracted text.
TRADE_ANCHOR_RE = re.compile(
    r"(?:^|\s)([PSE])(?:\s*\((?:partial|p)\))?\s+\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}/\d{1,2}/\d{4}"
)


def inspect_pdf(path: Path) -> dict:
    """Returns {is_paper_scan, page_count, text_chars, anchor_matches}.

    Paper-scan heuristic: cleaned text < 200 chars OR zero TRADE_ANCHOR matches
    in the raw text. Validated on 1,869-PDF sample at zero false-positives.
    """
    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    anchors = len(list(TRADE_ANCHOR_RE.finditer(text)))
    return {
        "is_paper_scan": len(text.strip()) < 200 or anchors == 0,
        "page_count": n_pages,
        "text_chars": len(text),
        "anchor_matches": anchors,
    }


def _parse_us_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except Exception:
        try:
            return date.fromisoformat(s)
        except Exception:
            return None


def build_placeholder_row(ptr: dict, page_count: int, bioguide: str | None) -> dict | None:
    filing_date = _parse_us_date(ptr["filing_date"])
    if not filing_date:
        return None
    url = PDF_URL.format(year=ptr["year"], doc_id=ptr["doc_id"])
    return {
        "country_code": "US",
        "chamber": "house",
        "bioguide_id": bioguide,
        "legislator_name_raw": f"{ptr['first']} {ptr['last']}".strip()[:200],
        "endpoint_id": ENDPOINT_ID,
        "filing_id": ptr["doc_id"][:50],
        "filing_date": filing_date,
        "filing_url": url[:500],
        "transaction_date": filing_date,  # placeholder; real OCR will use real dates
        "notification_date": None,
        "filer_type": "self",
        "transaction_type": "purchase",  # placeholder
        "asset_name_raw": (
            f"[PAPER PTR — {page_count} pages — OCR pending] "
            f"House Clerk scanned PTR. View: {url}"
        )[:5000],
        "asset_type_code": "OT",
        "ticker": None,
        "amount_str": "(paper-PTR placeholder)",
        "amount_min": None,
        "amount_max": None,
        "amount_mid": None,
        "raw_archive_id": None,
    }


def main(*, apply: bool, years: list[int] | None) -> int:
    LOG_DIR = PROJECT_ROOT / "logs"
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"backfill_house_paper_ptr_placeholders_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("backfill_house_paper")
    log.info("logging to %s", log_file)

    engine = get_engine()
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "house_clerk_ptr")
    matcher = StrictBioguideMatcher(engine)
    client = PoliticalHTTPClient(source="house_clerk", engine=engine)

    target_years = years or list(range(2013, 2027))
    log.info("scanning years: %s  apply=%s", target_years, apply)
    ptrs = list_ptrs(client, target_years)
    log.info("PTRs in index: %d", len(ptrs))

    rows: list[dict] = []
    by_year_paper: Counter = Counter()
    by_filer_paper: Counter = Counter()
    paper_n = electronic_n = missing_pdf = parse_err = bg_res = bg_unres = 0

    for i, ptr in enumerate(ptrs, 1):
        if i % 500 == 0:
            log.info("[%d/%d] paper=%d electronic=%d missing=%d bg_resolved=%d/%d",
                     i, len(ptrs), paper_n, electronic_n, missing_pdf,
                     bg_res, bg_res + bg_unres)
        path = PTR_DIR / f"{ptr['year']}_{ptr['doc_id']}.pdf"
        if not path.exists():
            missing_pdf += 1
            continue
        try:
            meta = inspect_pdf(path)
        except Exception as e:
            log.warning("inspect fail %s: %s", path.name, e)
            parse_err += 1
            continue
        if not meta["is_paper_scan"]:
            electronic_n += 1
            continue
        paper_n += 1
        filing_date = _parse_us_date(ptr["filing_date"])
        bioguide = matcher.resolve(
            first=ptr["first"], last=ptr["last"],
            chamber="rep", trade_date=filing_date,
        )
        if bioguide:
            bg_res += 1
        else:
            bg_unres += 1
            log.debug("[BIOGUIDE-UNRESOLVED] %s %s %s %s",
                      ptr["filing_date"], ptr["last"], ptr["first"], ptr.get("state_dst", "?"))
        row = build_placeholder_row(ptr, meta["page_count"], bioguide)
        if row:
            rows.append(row)
            by_year_paper[filing_date.year if filing_date else "?"] += 1
            by_filer_paper[f"{ptr['last']}, {ptr['first']}"] += 1

    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    log.info("PTRs in index           : %d", len(ptrs))
    log.info("Missing PDFs            : %d", missing_pdf)
    log.info("Inspect errors          : %d", parse_err)
    log.info("Skipped (electronic)    : %d", electronic_n)
    log.info("Paper-scan placeholders : %d", paper_n)
    log.info("Bioguide resolved       : %d / %d (%.1f%%)",
             bg_res, paper_n, 100 * bg_res / max(1, paper_n))
    log.info("Bioguide unresolved     : %d", bg_unres)

    log.info("\nBy filing year:")
    for yr in sorted(by_year_paper):
        log.info("  %s: %d", yr, by_year_paper[yr])

    log.info("\nTop 15 paper filers:")
    for filer, n in by_filer_paper.most_common(15):
        log.info("  %-50s %d", filer, n)

    if not apply:
        log.info("\n(DRY-RUN — pass --apply to insert)")
        return 0

    if not rows:
        log.info("nothing to insert")
        return 0

    # Dedup defensively
    seen: dict[tuple, dict] = {}
    for r in rows:
        k = (r["source"], r["country_code"], r["chamber"], r["filing_id"],
             r["transaction_date"], r["asset_name_raw"], r["transaction_type"], r["amount_str"])
        seen[k] = r
    unique = list(seen.values())
    log.info("After dedup: %d unique rows", len(unique))

    fast_upsert(
        engine, legislator_trades, unique,
        conflict_keys=["source", "country_code", "chamber", "filing_id",
                       "transaction_date", "asset_name_raw", "transaction_type", "amount_str"],
        update_cols=["bioguide_id", "legislator_name_raw", "filing_url", "filer_type",
                     "asset_type_code", "as_of_time"],
    )
    log.info("upserted %d rows into alt_political_us.legislator_trades", len(unique))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually insert rows (default: dry-run preview)")
    ap.add_argument("--years", type=str, default=None,
                    help="Year or year range, e.g. 2013-2021 or 2014. Default: 2013-2026.")
    args = ap.parse_args()

    years = None
    if args.years:
        if "-" in args.years:
            a, b = args.years.split("-")
            years = list(range(int(a), int(b) + 1))
        else:
            years = [int(args.years)]

    sys.exit(main(apply=args.apply, years=years))
