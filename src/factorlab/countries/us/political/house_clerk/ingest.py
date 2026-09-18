"""House Clerk PTR full ingestion: index → fetch PDFs → parse → upsert.

Run:
    python -m factorlab.countries.us.political.house_clerk.ingest --years 2024-2026
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.countries.us.political._asset_classifier import AssetClassifier
from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._client import DATA_ROOT, PoliticalHTTPClient
from factorlab.countries.us.political._db import fast_upsert
from factorlab.countries.us.political._state import State
from factorlab.countries.us.political.house_clerk.index import list_ptrs
from factorlab.countries.us.political.house_clerk.parser import parse_pdf
from factorlab.storage.schemas.alt_political_us import legislator_trades

log = logging.getLogger(__name__)

PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

OWNER_MAP = {"SP": "spouse", "JT": "joint", "DC": "dependent_child", "JR": "junior", None: "self"}
TX_TYPE_MAP = {"P": "purchase", "S": "sale_full", "E": "exchange"}


@dataclass
class IngestResult:
    ptrs_attempted: int = 0
    ptrs_with_trades: int = 0
    ptrs_zero_trades: int = 0
    ptrs_paper_scan: int = 0
    ptrs_fetch_failed: int = 0
    ptrs_parse_failed: int = 0
    trades_inserted: int = 0
    bioguide_resolved: int = 0
    bioguide_unresolved: int = 0
    files_fetched: list[str] = field(default_factory=list)


# ---- bioguide fuzzy match -------------------------------------------------

# Strict bioguide resolution lives in _bioguide.StrictBioguideMatcher.
# House Clerk filers always have a state_dst (e.g. 'GA12') — we extract the
# state code and pass it as an additional disambiguator.


# ---- date parsing ----------------------------------------------------------

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


# ---- the PDF batch loop ----------------------------------------------------

def _fetch_pdf(client: PoliticalHTTPClient, year: str, doc_id: str) -> Path | None:
    try:
        save_as = Path("ptrs") / f"{year}_{doc_id}.pdf"
        body, path = client.get(PDF_URL.format(year=year, doc_id=doc_id),
                                save_as=save_as, ext="pdf")
        return path
    except Exception as e:
        log.warning("[house_clerk] PDF fetch fail year=%s doc=%s: %s", year, doc_id, e)
        return None


def _paper_placeholder_row(
    *, ptr: dict, page_count: int, bioguide_id: str | None, endpoint_id: int,
) -> dict:
    """Build a placeholder row for a paper-scanned PTR (OCR pending).

    The unique asset_name_raw lets future OCR'd real trade rows for the same
    filing_id coexist alongside the placeholder.
    """
    filing_date = _parse_us_date(ptr["filing_date"])
    url = PDF_URL.format(year=ptr["year"], doc_id=ptr["doc_id"])
    return {
        "country_code": "US",
        "chamber": "house",
        "bioguide_id": bioguide_id,
        "legislator_name_raw": f"{ptr['first']} {ptr['last']}".strip()[:200],
        "endpoint_id": endpoint_id,
        "filing_id": ptr["doc_id"][:50],
        "filing_date": filing_date,
        "filing_url": url[:500],
        "transaction_date": filing_date,  # placeholder; OCR will set the real date
        "notification_date": None,
        "filer_type": "self",
        "transaction_type": "purchase",  # placeholder
        "asset_name_raw": (
            f"[PAPER PTR — {page_count} pages — OCR pending] "
            f"House Clerk scanned PTR. View: {url}"
        )[:5000],
        "asset_type_code": None,  # genuinely unknown until OCR; NULL beats wrong
        "ticker": None,
        "amount_str": "(paper-PTR placeholder)",
        "amount_min": None,
        "amount_max": None,
        "amount_mid": None,
        "raw_archive_id": None,
    }


def _to_trade_row(
    *, ptr: dict, parsed: dict, bioguide_id: str | None, raw_archive_id: str | None,
    endpoint_id: int, classifier: AssetClassifier | None = None,
) -> dict:
    # Parser-extracted ticker + code first; fall back to classifier if either is missing
    parsed_ticker = parsed.get("ticker")
    parsed_code = parsed.get("asset_type_code") if parsed.get("asset_type_code") and len(parsed["asset_type_code"]) <= 2 else None
    if classifier and (not parsed_ticker or not parsed_code):
        cls = classifier.classify(parsed.get("asset_name_raw") or "")
        if not parsed_ticker and cls.ticker:
            parsed_ticker = cls.ticker
        if not parsed_code and cls.asset_type_code:
            parsed_code = cls.asset_type_code
        # STRICT POLICY: NULL beats wrong. Don't claim "ST" just from a paren
        # ticker shape — could be ETF, mutual fund, warrant, foreign listing, etc.
    return {
        "country_code": "US",
        "chamber": "house",
        "bioguide_id": bioguide_id,
        "legislator_name_raw": f"{ptr['first']} {ptr['last']}".strip()[:200],
        "endpoint_id": endpoint_id,
        "filing_id": ptr["doc_id"],
        "filing_date": _parse_us_date(ptr["filing_date"]),
        "filing_url": PDF_URL.format(year=ptr["year"], doc_id=ptr["doc_id"])[:500],
        "transaction_date": _parse_us_date(parsed["tx_date"]),
        "notification_date": _parse_us_date(parsed["notif_date"]),
        "filer_type": OWNER_MAP.get(parsed.get("owner_code"), "self"),
        "transaction_type": TX_TYPE_MAP.get(parsed["tx_type"], "purchase"),
        "asset_name_raw": parsed["asset_name_raw"][:5000],
        "asset_type_code": parsed_code,
        "ticker": parsed_ticker,
        "amount_str": (parsed.get("amount_str") or "")[:40],
        "amount_min": parsed.get("amount_min"),
        "amount_max": parsed.get("amount_max"),
        "amount_mid": parsed.get("amount_mid"),
        "raw_archive_id": raw_archive_id,
    }


def ingest_house_clerk(
    engine: Engine,
    *,
    years: list[int] | None = None,
    limit_per_year: int | None = None,
    limit_total: int | None = None,
    dry_run: bool = False,
) -> IngestResult:
    """Backfill House PTR trades for the given years.

    Args:
        years: list of years to ingest. Default: 2013-2026 (full PTR history).
            Note: PTRs (FilingType='P') start in 2013 — STOCK Act took effect
            Aug 2012 and the first PTRs were filed late-2012/early-2013.
            Pre-2013 House Clerk filings are annual financial disclosures
            (FilingType='O'/'A'), not transaction-level — different schema.
        limit_per_year: cap PTRs per year for testing.
        limit_total: hard stop after N PTRs (across all years).
        dry_run: parse but don't write to DB.
    """
    years = years or list(range(2013, 2027))
    log.info("[house_clerk] starting years=%s limit_per_year=%s limit_total=%s",
             years, limit_per_year, limit_total)

    # Resolve our endpoint FK once; every row dict carries this id
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "house_clerk_ptr")

    client = PoliticalHTTPClient(source="house_clerk", engine=engine)
    state = State(source="house_clerk_ptr")
    matcher = StrictBioguideMatcher(engine)
    classifier = AssetClassifier()
    res = IngestResult()

    # 1. Build the master PTR list across all years
    ptrs = list_ptrs(client, years, limit_per_year=limit_per_year)
    if limit_total:
        ptrs = ptrs[:limit_total]
    res.ptrs_attempted = len(ptrs)
    log.info("[house_clerk] %d PTRs in scope", len(ptrs))

    # 2. Walk PTRs, fetch + parse + buffer rows for batched upsert
    pending: list[dict] = []
    BATCH = 5000
    for i, ptr in enumerate(ptrs, 1):
        key = f"{ptr['year']}/{ptr['doc_id']}"
        if state.is_done(key):
            continue
        path = _fetch_pdf(client, ptr["year"], ptr["doc_id"])
        if not path:
            res.ptrs_fetch_failed += 1
            continue
        try:
            parsed = parse_pdf(path)
        except Exception as e:
            log.warning("[house_clerk] parse fail %s: %s", key, e)
            res.ptrs_parse_failed += 1
            continue
        meta = parsed["meta"]
        trades = parsed["trades"]

        if meta["kind"] == "paper_scan":
            res.ptrs_paper_scan += 1
            filing_date = _parse_us_date(ptr["filing_date"])
            bioguide = matcher.resolve(
                first=ptr["first"], last=ptr["last"],
                chamber="rep", trade_date=filing_date,
            )
            if bioguide:
                res.bioguide_resolved += 1
            else:
                res.bioguide_unresolved += 1
            pending.append(_paper_placeholder_row(
                ptr=ptr, page_count=meta["page_count"], bioguide_id=bioguide,
                endpoint_id=ENDPOINT_ID,
            ))
            state.mark_done(key)
        elif not trades:
            # Electronic kind but parser extracted nothing — text-rich but
            # parser-incompatible. Counted separately so the audit gap is visible;
            # detailed handling lives in scripts/load_house_clerk_historical.py.
            res.ptrs_zero_trades += 1
            state.mark_done(key)
            continue
        else:
            res.ptrs_with_trades += 1
            # Use the trade_date of the FIRST trade for term-overlap (close enough;
            # all trades in a single PTR are filed in the same window)
            first_tx_date = _parse_us_date(trades[0].get("tx_date") or "")
            bioguide = matcher.resolve(
                first=ptr["first"], last=ptr["last"],
                chamber="rep", trade_date=first_tx_date,
            )
            if bioguide:
                res.bioguide_resolved += 1
            else:
                res.bioguide_unresolved += 1

            for t in trades:
                pending.append(_to_trade_row(
                    ptr=ptr, parsed=t, bioguide_id=bioguide, raw_archive_id=None,
                    endpoint_id=ENDPOINT_ID, classifier=classifier,
                ))
            state.mark_done(key)

        # Flush periodically + log progress
        if len(pending) >= BATCH:
            if not dry_run:
                _flush(engine, pending)
            res.trades_inserted += len(pending)
            pending = []
            state.flush()

        if i % 50 == 0:
            log.info("[house_clerk] %d/%d PTRs (with-trades=%d paper=%d zero=%d failed=%d  pending=%d)",
                     i, len(ptrs), res.ptrs_with_trades, res.ptrs_paper_scan,
                     res.ptrs_zero_trades,
                     res.ptrs_fetch_failed + res.ptrs_parse_failed, len(pending))

        # Polite delay (the cache means re-runs are instant; this only matters for new fetches)
        time.sleep(0.05)

    # Final flush
    if pending and not dry_run:
        _flush(engine, pending)
        res.trades_inserted += len(pending)
    state.flush()

    log.info("[house_clerk] done: %s", res)
    return res


def _flush(engine: Engine, rows: list[dict]) -> None:
    """Upsert a batch of trade rows. Idempotent via the dedup UNIQUE constraint.

    Dedups within batch (some PTRs list the same trade twice — same date,
    same asset, same amount — and the UPSERT can't update one target twice
    in a single statement).
    """
    seen: dict[tuple, dict] = {}
    for r in rows:
        k = (r["endpoint_id"], r["country_code"], r["chamber"], r["filing_id"],
             r["transaction_date"], r["asset_name_raw"], r["transaction_type"],
             r["amount_str"])
        seen[k] = r  # last-wins
    unique = list(seen.values())
    fast_upsert(
        engine, legislator_trades, unique,
        conflict_keys=["endpoint_id", "country_code", "chamber", "filing_id",
                       "transaction_date", "asset_name_raw", "transaction_type",
                       "amount_str"],
        update_cols=["bioguide_id", "legislator_name_raw", "filing_url",
                     "notification_date", "filer_type", "asset_type_code",
                     "ticker", "amount_min", "amount_max", "amount_mid",
                     "raw_archive_id", "as_of_time"],
    )


# ---- CLI -------------------------------------------------------------------

def _parse_year_arg(s: str) -> list[int]:
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s)]


if __name__ == "__main__":
    import sys
    from datetime import datetime, timezone

    from factorlab.shared.paths import REPO_ROOT as PROJECT_ROOT

    LOG_DIR = PROJECT_ROOT / "logs"
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"us_political_house_clerk_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log.info("logging to %s", log_file)
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2013-2026", help="Year or year range, e.g. 2024 or 2013-2026 (PTRs start 2013)")
    ap.add_argument("--limit-per-year", type=int, default=None)
    ap.add_argument("--limit-total", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from factorlab.storage.db import get_engine
    res = ingest_house_clerk(
        get_engine(),
        years=_parse_year_arg(args.years),
        limit_per_year=args.limit_per_year,
        limit_total=args.limit_total,
        dry_run=args.dry_run,
    )
    log.info("IngestResult: %s", res)
