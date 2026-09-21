"""Senate eFD ingestion → legislator_trades.

Run (must be on local machine — Akamai blocks cloud):
    python -m factorlab.countries.us.political.senate_efd.ingest --from 2026-04-01 --to 2026-04-30
    python -m factorlab.countries.us.political.senate_efd.ingest --from 2020-01-01 --to 2026-04-30   # full backfill
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.countries.us.political._asset_classifier import AssetClassifier
from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._db import fast_upsert
from factorlab.countries.us.political.senate_efd.parser import parse_html
from factorlab.countries.us.political.senate_efd.scraper import run_scrape
from factorlab.storage.schemas.alt_political_us import legislator_trades

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    filings_attempted: int = 0
    html_filings: int = 0
    paper_filings: int = 0
    trades_inserted: int = 0
    bioguide_resolved: int = 0
    bioguide_unresolved: int = 0
    notes: list[str] = field(default_factory=list)


# Strict bioguide resolution lives in _bioguide.StrictBioguideMatcher.


def _parse_us_or_iso_date(s: str) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _filing_id_from_url(url: str | None) -> str:
    """Extract UUID from /search/view/ptr/{uuid}/ or /search/view/paper/{uuid}/.
    That's our filing_id.
    """
    if not url:
        return ""
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else ""


def _paper_placeholder_row(manifest: dict, matcher, endpoint_id: int) -> dict | None:
    """Build one placeholder row for a paper-filed PTR (OCR deferred).

    The row records the filing's existence so we can:
      - track which paper PTRs we've seen vs. need to OCR
      - resolve bioguide for the filer
      - join paper-PTR-only senators into the same legislator_trades schema

    When OCR happens later, real trade rows get inserted alongside (different
    asset_name_raw values keep the UNIQUE constraint happy).
    """
    filing_id = _filing_id_from_url(manifest.get("link"))
    filing_date = _parse_us_or_iso_date(manifest["filed_date"])
    if not filing_id or not filing_date:
        return None
    bioguide = matcher.resolve(
        first=manifest.get("first"), last=manifest.get("last"),
        chamber="sen", trade_date=filing_date,
    )
    page_count = manifest.get("page_count") or "?"
    return {
        "country_code": "US",
        "chamber": "senate",
        "bioguide_id": bioguide,
        "legislator_name_raw": (manifest.get("office") or
                                f"{manifest.get('first', '')} {manifest.get('last', '')}".strip())[:200],
        "endpoint_id": endpoint_id,
        "filing_id": filing_id[:50],
        "filing_date": filing_date,
        "filing_url": (manifest.get("link") or "")[:500] or None,
        "transaction_date": filing_date,  # placeholder — real trades will use real dates
        "notification_date": None,
        # Strict-NULL: don't fabricate filer/tx values for a placeholder row.
        # Real values arrive when OCR populates the actual trade rows.
        "filer_type": None,
        "transaction_type": None,
        "asset_name_raw": (
            f"[PAPER PTR — {page_count} pages — OCR pending] "
            f"Senate eFD paper-filed PTR. View: {manifest.get('link', '')}"
        )[:5000],
        "asset_type_code": None,  # NULL beats fabricated 'OT'
        "ticker": None,
        "amount_str": "(paper-PTR placeholder)",
        "amount_min": None,
        "amount_max": None,
        "amount_mid": None,
        "raw_archive_id": None,
    }


def ingest_senate_efd(
    engine: Engine,
    *,
    date_from: str,
    date_to: str,
    headless: bool = True,
    max_filings: int | None = None,
    dry_run: bool = False,
) -> IngestResult:
    """Scrape + parse + upsert Senate eFD PTRs in a date window.

    Args:
        date_from / date_to: 'YYYY-MM-DD' or 'MM/DD/YYYY'
        headless: True for production cron
        max_filings: cap for testing
    """
    log.info("[senate_efd] starting from=%s to=%s headless=%s", date_from, date_to, headless)
    # Resolve our endpoint FK once; every row dict carries this id
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "senate_efd_ptr")
    res = IngestResult()
    matcher = StrictBioguideMatcher(engine)
    classifier = AssetClassifier()

    # Backfill any missing paper-PTR GIFs from earlier runs. Plain HTTP to
    # the public CDN — cheap, no Akamai, idempotent (skips already-cached
    # GIFs). Runs before Playwright so OCR-readiness improves even if the
    # current scrape window finds no new paper filings.
    from factorlab.countries.us.political.senate_efd.scraper import (
        backfill_paper_gifs_from_sidecars,
    )
    n_paper_filings_seen, n_gifs_pulled = backfill_paper_gifs_from_sidecars()
    if n_paper_filings_seen:
        log.info("[senate_efd] paper-GIF backfill: scanned=%d filings, "
                 "downloaded=%d new GIFs", n_paper_filings_seen, n_gifs_pulled)

    # Normalize dates to MM/DD/YYYY for the form
    def _norm(d: str) -> str:
        if "-" in d:
            y, m, dd = d.split("-")
            return f"{int(m):02d}/{int(dd):02d}/{y}"
        return d

    scrape = run_scrape(
        date_from=_norm(date_from), date_to=_norm(date_to),
        headless=headless, max_filings=max_filings,
    )
    res.filings_attempted = len(scrape.search_results)

    pending: list[dict] = []
    for manifest in scrape.downloaded:
        if manifest["file_type"] == "paper-scan":
            res.paper_filings += 1
            res.notes.append(
                f"paper-scan {manifest['filed_date']} {manifest['last']} {manifest['first']} "
                f"({manifest.get('page_count', '?')} pages — OCR deferred)"
            )
            # Write a placeholder row so the paper PTR is tracked in the DB.
            # When OCR happens later, real trade rows get inserted alongside.
            placeholder = _paper_placeholder_row(manifest, matcher, ENDPOINT_ID)
            if placeholder:
                pending.append(placeholder)
            continue
        res.html_filings += 1

        try:
            trades = parse_html(manifest["saved_path"])
        except Exception as e:
            log.warning("[senate_efd] parse fail %s: %s", manifest["saved_path"], e)
            continue

        filing_id = _filing_id_from_url(manifest.get("link"))
        filing_date = _parse_us_or_iso_date(manifest["filed_date"])

        # Use first trade's date for term-overlap; fall back to filing_date if empty
        first_trade_date = (
            _parse_us_or_iso_date(trades[0]["tx_date_str"]) if trades else None
        ) or filing_date
        bioguide = matcher.resolve(
            first=manifest["first"], last=manifest["last"],
            chamber="sen",
            trade_date=first_trade_date,
        )
        if bioguide:
            res.bioguide_resolved += 1
        else:
            res.bioguide_unresolved += 1
        for t in trades:
            tx_date = _parse_us_or_iso_date(t["tx_date_str"])
            if not tx_date or not filing_date:
                continue
            ticker = t.get("ticker")
            if ticker and (len(ticker) > 10 or " " in ticker):
                ticker = None
            # Classify asset_type_code + recover ticker from asset_name_raw if missing
            asset_name_raw = t.get("asset_name_raw") or ""
            cls = classifier.classify(asset_name_raw)
            asset_type_code = cls.asset_type_code
            if not ticker and cls.ticker:
                ticker = cls.ticker
            # STRICT-NULL: don't infer asset_type_code='ST' from a ticker shape
            # alone — tickers can be ETF/ADR/warrants/foreign listings. Leave
            # NULL when the source PDF doesn't declare a code; the classifier's
            # high-confidence resolution will populate via a separate path.
            pending.append({
                "country_code": "US",
                "chamber": "senate",
                "bioguide_id": bioguide,
                "legislator_name_raw": (manifest.get("office") or
                                        f"{manifest['first']} {manifest['last']}")[:200],
                "endpoint_id": ENDPOINT_ID,
                "filing_id": (filing_id or "")[:50],
                "filing_date": filing_date,
                "filing_url": (manifest.get("link") or "")[:500] or None,
                "transaction_date": tx_date,
                "notification_date": None,  # eFD HTML doesn't surface it
                "filer_type": t["owner"],
                "transaction_type": t["tx_type"],
                "asset_name_raw": (t.get("asset_name_raw") or "")[:5000],
                "asset_type_code": asset_type_code,
                "ticker": ticker,
                "amount_str": (t.get("amount_str") or "")[:40],
                "amount_min": t.get("amount_min"),
                "amount_max": t.get("amount_max"),
                "amount_mid": t.get("amount_mid"),
                "raw_archive_id": None,
            })

    if pending and not dry_run:
        # Dedup defensively
        seen = set()
        unique = []
        for r in pending:
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
        res.trades_inserted = len(unique)

    log.info("[senate_efd] done: %s", res)
    return res


if __name__ == "__main__":
    import sys
    from datetime import datetime, timezone

    from factorlab.shared.paths import REPO_ROOT as PROJECT_ROOT

    LOG_DIR = PROJECT_ROOT / "logs"
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"us_political_senate_efd_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"
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
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD or MM/DD/YYYY")
    ap.add_argument("--to", dest="date_to", required=True)
    ap.add_argument("--headed", action="store_true", help="Show browser (debugging)")
    ap.add_argument("--max-filings", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from factorlab.storage.db import get_engine
    res = ingest_senate_efd(
        get_engine(),
        date_from=args.date_from, date_to=args.date_to,
        headless=not args.headed,
        max_filings=args.max_filings,
        dry_run=args.dry_run,
    )
    log.info("IngestResult: %s", res)
