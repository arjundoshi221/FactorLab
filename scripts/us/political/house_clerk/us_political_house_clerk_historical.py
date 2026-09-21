"""One-time historical load for House Clerk PTRs (2013-2026) — tiered, parallel, vetted.

Pipeline:
  1. multiprocessing.Pool parses every PDF (parallelism = cpu_count() - 1).
  2. Single-process classifier sorts results into 4 tiers:
       Tier 1: electronic + valid trades + valid bioguide + valid instrument → AUTO-INSERT
       Tier 2: electronic + trades but quality gate failed → CLAUDE REVIEW QUEUE
       Tier 3: paper_scan (heuristic) → PLACEHOLDER ROW
       Tier 4: text-rich but parser produced 0 trades / errored → CLAUDE REVIEW QUEUE
  3. CSV outputs under logs/house_clerk_historical_load_<UTC>/.
  4. With --apply, tier 1 + tier 3 rows are upserted.
     Tiers 2 + 4 are CSV-logged for Claude direct-read in a follow-up phase.

Run:
    python scripts/load_house_clerk_historical.py --dry-run             # full pipeline, no DB writes
    python scripts/load_house_clerk_historical.py --apply               # upsert tier 1 + 3
    python scripts/load_house_clerk_historical.py --apply --years 2014-2021
    python scripts/load_house_clerk_historical.py --apply --workers 4
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from multiprocessing import Pool, cpu_count
from pathlib import Path

# Path layout: scripts/us/political/house_clerk/<file>.py
#   parents[0]=house_clerk, [1]=political, [2]=us, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import text

from factorlab.countries.us.political._asset_classifier import AssetClassifier
from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._client import DATA_ROOT, PoliticalHTTPClient
from factorlab.countries.us.political._db import fast_upsert
from factorlab.countries.us.political.house_clerk.index import list_ptrs
from factorlab.countries.us.political.house_clerk.parser import parse_pdf
from factorlab.storage.db import get_engine
from factorlab.storage.schemas.alt_political_us import legislator_trades

PTR_DIR = DATA_ROOT / "house_clerk" / "ptrs"
PDF_URL_TPL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

OWNER_MAP = {"SP": "spouse", "JT": "joint", "DC": "dependent_child", "JR": "junior", None: "self"}
TX_TYPE_MAP = {"P": "purchase", "S": "sale_full", "E": "exchange"}

# Header markers that indicate a polluted asset_name_raw (Phase C quality gate)
HEADER_POLLUTION_MARKERS = (
    "filer information", "transactions", "iD owner", "id owner",
    "transaction date", "notification", "filing id",
    "periodic transaction", "eriodic ransaction",
    "type date", "amount cap",
)

# Regexes used by quality gates
TICKER_VALID_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


# ---- multiprocessing worker -------------------------------------------------

def parse_one(args: tuple) -> dict:
    """Worker: parse a single PDF. Pure function, picklable.

    Returns dict with: path, ptr (index metadata), ok (bool),
    plus on success: trades + meta from parse_pdf.
    On failure: error string.
    """
    path_str, ptr = args
    path = Path(path_str)
    try:
        result = parse_pdf(path)
        return {
            "path": path_str, "ptr": ptr, "ok": True,
            "trades": result["trades"], "meta": result["meta"],
        }
    except Exception as e:
        return {
            "path": path_str, "ptr": ptr, "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }


# ---- helpers ---------------------------------------------------------------

def _parse_us_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except Exception:
        try:
            return date.fromisoformat(s)
        except Exception:
            return None


def _trade_row(*, ptr: dict, parsed: dict, bioguide_id: str | None,
               classifier: AssetClassifier) -> dict:
    """Build a legislator_trades row from a parsed trade dict."""
    parsed_ticker = parsed.get("ticker")
    parsed_code = parsed.get("asset_type_code") if (
        parsed.get("asset_type_code") and len(parsed["asset_type_code"]) <= 2
    ) else None
    if not parsed_ticker or not parsed_code:
        cls = classifier.classify(parsed.get("asset_name_raw") or "")
        if not parsed_ticker and cls.ticker:
            parsed_ticker = cls.ticker
        if not parsed_code and cls.asset_type_code:
            parsed_code = cls.asset_type_code
        # STRICT POLICY: don't claim "ST" just because there's a ticker-shaped
        # paren — could be ETF, mutual fund, warrant, foreign listing, or noise.
        # NULL is preferred over a wrong classification.
    return {
        "country_code": "US",
        "chamber": "house",
        "bioguide_id": bioguide_id,
        "legislator_name_raw": f"{ptr['first']} {ptr['last']}".strip()[:200],
        "endpoint_id": ENDPOINT_ID,
        "filing_id": ptr["doc_id"][:50],
        "filing_date": _parse_us_date(ptr["filing_date"]),
        "filing_url": PDF_URL_TPL.format(year=ptr["year"], doc_id=ptr["doc_id"])[:500],
        "transaction_date": _parse_us_date(parsed["tx_date"]),
        "notification_date": _parse_us_date(parsed["notif_date"]),
        "filer_type": OWNER_MAP.get(parsed.get("owner_code"), "self"),
        "transaction_type": TX_TYPE_MAP.get(parsed["tx_type"], "purchase"),
        "asset_name_raw": (parsed["asset_name_raw"] or "")[:5000],
        "asset_type_code": parsed_code,
        "ticker": parsed_ticker,
        "amount_str": (parsed.get("amount_str") or "")[:40],
        "amount_min": parsed.get("amount_min"),
        "amount_max": parsed.get("amount_max"),
        "amount_mid": parsed.get("amount_mid"),
        "raw_archive_id": None,
    }


def _paper_placeholder_row(*, ptr: dict, page_count: int,
                           bioguide_id: str | None) -> dict:
    filing_date = _parse_us_date(ptr["filing_date"])
    url = PDF_URL_TPL.format(year=ptr["year"], doc_id=ptr["doc_id"])
    return {
        "country_code": "US",
        "chamber": "house",
        "bioguide_id": bioguide_id,
        "legislator_name_raw": f"{ptr['first']} {ptr['last']}".strip()[:200],
        "endpoint_id": ENDPOINT_ID,
        "filing_id": ptr["doc_id"][:50],
        "filing_date": filing_date,
        "filing_url": url[:500],
        "transaction_date": filing_date,
        "notification_date": None,
        "filer_type": "self",
        "transaction_type": "purchase",
        "asset_name_raw": (
            f"[PAPER PTR — {page_count} pages — OCR pending] "
            f"House Clerk scanned PTR. View: {url}"
        )[:5000],
        "asset_type_code": None,  # genuinely unknown until OCR / Claude review
        "ticker": None,
        "amount_str": "(paper-PTR placeholder)",
        "amount_min": None,
        "amount_max": None,
        "amount_mid": None,
        "raw_archive_id": None,
    }


def _quality_gate(row: dict, *, valid_codes: set[str]) -> tuple[bool, list[str]]:
    """Apply quality gates. Returns (passed, reasons_for_failure)."""
    reasons = []
    if not row["bioguide_id"]:
        reasons.append("bg_unresolved")

    asset = (row["asset_name_raw"] or "").lower()
    if len(asset.strip()) < 3:
        reasons.append("asset_too_short")
    if any(marker in asset for marker in HEADER_POLLUTION_MARKERS):
        reasons.append("header_pollution")

    code = row.get("asset_type_code") or ""
    if code and code not in valid_codes:
        reasons.append("bad_asset_type")

    ticker = row.get("ticker")
    if ticker and not TICKER_VALID_RE.match(ticker):
        reasons.append("bad_ticker")

    if not row.get("transaction_date"):
        reasons.append("no_tx_date")
    if not row.get("amount_str"):
        reasons.append("no_amount")

    return (len(reasons) == 0, reasons)


# ---- main pipeline ----------------------------------------------------------

def main(*, apply: bool, workers: int | None, years: list[int] | None) -> int:
    workers = workers or max(1, cpu_count() - 1)

    # Output dir under logs/
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    OUT_DIR = PROJECT_ROOT / "logs" / f"house_clerk_historical_load_{run_id}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log_file = OUT_DIR / "run.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("hc_historical")
    log.info("RUN_ID=%s  workers=%d  apply=%s  years=%s",
             run_id, workers, apply, years)
    log.info("OUT_DIR=%s", OUT_DIR)

    engine = get_engine()
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "house_clerk_ptr")
    matcher = StrictBioguideMatcher(engine)
    classifier = AssetClassifier()
    client = PoliticalHTTPClient(source="house_clerk", engine=engine)

    # Lookup valid asset_type codes for quality gate
    with engine.connect() as c:
        valid_codes = {r[0] for r in c.execute(
            text("SELECT code FROM alt_political_us.asset_type_codes")
        ).all()}
    log.info("valid asset_type codes: %d", len(valid_codes))

    # === Phase B: multiprocessed parse ======================================
    target_years = years or list(range(2013, 2027))
    ptrs = list_ptrs(client, target_years)
    log.info("PTRs in index: %d", len(ptrs))

    work = []
    missing = 0
    for p in ptrs:
        path = PTR_DIR / f"{p['year']}_{p['doc_id']}.pdf"
        if path.exists():
            work.append((str(path), p))
        else:
            missing += 1
    log.info("workable PDFs: %d  (missing: %d)", len(work), missing)

    log.info("parsing with %d workers...", workers)
    t0 = datetime.now(timezone.utc)
    results: list[dict] = []
    with Pool(workers) as pool:
        for i, r in enumerate(pool.imap_unordered(parse_one, work, chunksize=20), 1):
            results.append(r)
            if i % 500 == 0:
                elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
                rate = i / max(1, elapsed)
                log.info("  parsed %d/%d  (%.1f files/s)", i, len(work), rate)
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    log.info("parse done: %d in %.1fs (%.1f files/s)",
             len(results), elapsed, len(results) / max(1, elapsed))

    # === Phase C: classify + quality gates ==================================
    tier1_rows: list[dict] = []   # auto-insert (clean trades)
    tier2_rows: list[dict] = []   # extracted but quality gate failed
    tier3_rows: list[dict] = []   # paper-scan placeholders
    tier4_csv: list[dict] = []    # Claude review queue (text-rich, no trades / errors)
    errors_csv: list[dict] = []

    bg_resolved = bg_unresolved = 0
    by_year_tier = Counter()  # (year, tier) -> count

    for r in results:
        ptr = r["ptr"]
        year = ptr["year"]

        if not r["ok"]:
            errors_csv.append({
                "year": year, "doc_id": ptr["doc_id"],
                "filer": f"{ptr['first']} {ptr['last']}",
                "error": r["error"],
            })
            tier4_csv.append({
                "year": year, "doc_id": ptr["doc_id"],
                "filer": f"{ptr['first']} {ptr['last']}", "state": ptr.get("state_dst", ""),
                "page_count": 0, "anchor_matches": 0, "text_chars_cleaned": 0,
                "reason": "parser_exception", "details": r["error"],
            })
            by_year_tier[(year, 4)] += 1
            continue

        meta = r["meta"]
        trades = r["trades"]
        filing_date = _parse_us_date(ptr["filing_date"])

        # Tier 3: paper scan
        if meta["kind"] == "paper_scan":
            bioguide = matcher.resolve(
                first=ptr["first"], last=ptr["last"],
                chamber="rep", trade_date=filing_date,
            )
            if bioguide:
                bg_resolved += 1
            else:
                bg_unresolved += 1
            row = _paper_placeholder_row(
                ptr=ptr, page_count=meta["page_count"], bioguide_id=bioguide,
            )
            tier3_rows.append(row)
            by_year_tier[(year, 3)] += 1
            continue

        # Tier 4: electronic but parser produced no trades
        if not trades:
            tier4_csv.append({
                "year": year, "doc_id": ptr["doc_id"],
                "filer": f"{ptr['first']} {ptr['last']}", "state": ptr.get("state_dst", ""),
                "page_count": meta["page_count"],
                "anchor_matches": meta["anchor_matches"],
                "text_chars_cleaned": meta["text_chars_cleaned"],
                "reason": "no_trades_extracted",
                "details": "",
            })
            by_year_tier[(year, 4)] += 1
            continue

        # Resolve bioguide once per PTR (using the first trade date)
        first_tx = _parse_us_date(trades[0].get("tx_date") or "")
        bioguide = matcher.resolve(
            first=ptr["first"], last=ptr["last"],
            chamber="rep", trade_date=first_tx or filing_date,
        )
        if bioguide:
            bg_resolved += 1
        else:
            bg_unresolved += 1

        # Apply quality gates per trade
        for t in trades:
            row = _trade_row(ptr=ptr, parsed=t, bioguide_id=bioguide, classifier=classifier)
            passed, reasons = _quality_gate(row, valid_codes=valid_codes)
            if passed:
                tier1_rows.append(row)
                by_year_tier[(year, 1)] += 1
            else:
                # Add review fields for Claude
                tier2_rows.append({
                    **{k: v for k, v in row.items() if k != "amount_min" and k != "amount_max" and k != "amount_mid"},
                    "_failure_reasons": "|".join(reasons),
                    "_pdf_path": r["path"],
                })
                by_year_tier[(year, 2)] += 1

    # === Phase D: write CSVs =================================================
    def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        log.info("wrote %d rows -> %s", len(rows), path.name)

    tier_fields = ["filing_id", "filing_date", "transaction_date",
                   "legislator_name_raw", "bioguide_id", "asset_name_raw",
                   "ticker", "asset_type_code", "transaction_type",
                   "amount_str", "filer_type", "filing_url"]
    write_csv(OUT_DIR / "tier1_inserted.csv", tier1_rows, tier_fields)

    tier2_fields = tier_fields + ["_failure_reasons", "_pdf_path"]
    write_csv(OUT_DIR / "tier2_review.csv", tier2_rows, tier2_fields)

    tier3_fields = ["filing_id", "filing_date", "legislator_name_raw",
                    "bioguide_id", "asset_name_raw", "filing_url"]
    write_csv(OUT_DIR / "tier3_paper.csv", tier3_rows, tier3_fields)

    write_csv(OUT_DIR / "tier4_claude_review.csv", tier4_csv,
              ["year", "doc_id", "filer", "state", "page_count",
               "anchor_matches", "text_chars_cleaned", "reason", "details"])

    write_csv(OUT_DIR / "errors.csv", errors_csv,
              ["year", "doc_id", "filer", "error"])

    # Summary
    summary_path = OUT_DIR / "summary.txt"
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(f"House Clerk historical load — run {run_id}\n")
        fh.write(f"Workers: {workers}  Apply: {apply}  Years: {target_years}\n\n")
        fh.write(f"PTRs in index : {len(ptrs)}\n")
        fh.write(f"Missing PDFs  : {missing}\n")
        fh.write(f"Parse errors  : {len(errors_csv)}\n")
        fh.write(f"Bioguide      : {bg_resolved}/{bg_resolved+bg_unresolved} resolved "
                 f"({100*bg_resolved/max(1,bg_resolved+bg_unresolved):.1f}%)\n\n")
        fh.write(f"Tier 1 (auto-insert)     : {len(tier1_rows)} trade rows\n")
        fh.write(f"Tier 2 (Claude review)   : {len(tier2_rows)} trade rows\n")
        fh.write(f"Tier 3 (paper-scan)      : {len(tier3_rows)} placeholder rows\n")
        fh.write(f"Tier 4 (Claude review)   : {len(tier4_csv)} PTR PDFs\n\n")
        fh.write("Per-year per-tier breakdown:\n")
        years_seen = sorted({y for (y, _) in by_year_tier})
        fh.write(f"  {'year':<6} {'T1':>6} {'T2':>5} {'T3':>5} {'T4':>5}\n")
        for y in years_seen:
            fh.write(f"  {y:<6} {by_year_tier[(y,1)]:>6} {by_year_tier[(y,2)]:>5} "
                     f"{by_year_tier[(y,3)]:>5} {by_year_tier[(y,4)]:>5}\n")
        fh.write("\n")
        # Tier 2 failure-reason histogram
        if tier2_rows:
            reason_counter: Counter = Counter()
            for r in tier2_rows:
                for reason in r["_failure_reasons"].split("|"):
                    reason_counter[reason] += 1
            fh.write("Tier 2 failure reasons:\n")
            for reason, n in reason_counter.most_common():
                fh.write(f"  {reason:<25} {n}\n")

    log.info("wrote summary -> %s", summary_path.name)
    log.info("=" * 60)
    log.info("Tier 1 (auto-insert)   : %d trade rows", len(tier1_rows))
    log.info("Tier 2 (Claude review) : %d trade rows", len(tier2_rows))
    log.info("Tier 3 (paper-scan)    : %d placeholder rows", len(tier3_rows))
    log.info("Tier 4 (Claude review) : %d PTR PDFs", len(tier4_csv))
    log.info("Bioguide resolved      : %d / %d (%.1f%%)",
             bg_resolved, bg_resolved + bg_unresolved,
             100 * bg_resolved / max(1, bg_resolved + bg_unresolved))

    if not apply:
        log.info("\n(DRY-RUN — pass --apply to upsert tier 1 + tier 3 rows)")
        return 0

    # === Phase G: upsert tier 1 + tier 3 ===================================
    if tier1_rows:
        # Dedup defensively
        seen: dict[tuple, dict] = {}
        for r in tier1_rows:
            k = (r["source"], r["country_code"], r["chamber"], r["filing_id"],
                 r["transaction_date"], r["asset_name_raw"], r["transaction_type"], r["amount_str"])
            seen[k] = r
        unique_t1 = list(seen.values())
        log.info("upserting tier 1: %d unique rows", len(unique_t1))
        fast_upsert(
            engine, legislator_trades, unique_t1,
            conflict_keys=["source", "country_code", "chamber", "filing_id",
                           "transaction_date", "asset_name_raw", "transaction_type", "amount_str"],
            update_cols=["bioguide_id", "legislator_name_raw", "filing_url",
                         "notification_date", "filer_type", "asset_type_code",
                         "ticker", "amount_min", "amount_max", "amount_mid",
                         "raw_archive_id", "as_of_time"],
        )
        log.info("tier 1 upsert done")

    if tier3_rows:
        seen3: dict[tuple, dict] = {}
        for r in tier3_rows:
            k = (r["source"], r["country_code"], r["chamber"], r["filing_id"],
                 r["transaction_date"], r["asset_name_raw"], r["transaction_type"], r["amount_str"])
            seen3[k] = r
        unique_t3 = list(seen3.values())
        log.info("upserting tier 3: %d unique rows", len(unique_t3))
        fast_upsert(
            engine, legislator_trades, unique_t3,
            conflict_keys=["source", "country_code", "chamber", "filing_id",
                           "transaction_date", "asset_name_raw", "transaction_type", "amount_str"],
            update_cols=["bioguide_id", "legislator_name_raw", "filing_url",
                         "filer_type", "asset_type_code", "as_of_time"],
        )
        log.info("tier 3 upsert done")

    log.info("Tier 2 (%d rows) + Tier 4 (%d PDFs) await Claude direct-read in next phase",
             len(tier2_rows), len(tier4_csv))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Upsert tier 1 + tier 3 rows (default: dry-run)")
    ap.add_argument("--workers", type=int, default=None,
                    help="Pool workers (default: cpu_count() - 1)")
    ap.add_argument("--years", type=str, default=None,
                    help="Year or year range, e.g. 2013-2021 or 2014. Default: 2013-2026.")
    args = ap.parse_args()

    yrs = None
    if args.years:
        if "-" in args.years:
            a, b = args.years.split("-")
            yrs = list(range(int(a), int(b) + 1))
        else:
            yrs = [int(args.years)]

    sys.exit(main(apply=args.apply, workers=args.workers, years=yrs))
