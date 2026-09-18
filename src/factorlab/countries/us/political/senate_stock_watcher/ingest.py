"""Senate Stock Watcher historical → legislator_trades.

The GitHub mirror is frozen since 2021-03 with data ending 2019-12-31.
Used as 2014-2019 backfill only; live Senate trades come from senate_efd/.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.countries.us.political._asset_classifier import AssetClassifier
from factorlab.countries.us.political._bioguide import StrictBioguideMatcher
from factorlab.countries.us.political._client import PoliticalHTTPClient
from factorlab.countries.us.political._db import fast_upsert
from factorlab.storage.schemas.alt_political_us import legislator_trades

log = logging.getLogger(__name__)

URL = (
    "https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/"
    "master/aggregate/all_transactions.json"
)

AMOUNT_BUCKETS = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, None),
}

OWNER_MAP_TEXT = {
    "Self": "self", "self": "self",
    "Spouse": "spouse", "Joint": "joint",
    "Child": "dependent_child", "Dependent Child": "dependent_child",
    "N/A": "self", "--": "self", "": "self",
}

TX_TYPE_MAP_TEXT = {
    "Purchase": "purchase",
    "Sale (Full)": "sale_full",
    "Sale (Partial)": "sale_partial",
    "Exchange": "exchange",
    # STRICT-NULL: 'N/A' was previously mapped to 'purchase' (fabricated). No
    # entry here means .get() returns None, which is correct for unknown.
}


@dataclass
class IngestResult:
    rows_attempted: int = 0
    trades_inserted: int = 0
    bioguide_resolved: int = 0
    bioguide_unresolved: int = 0


# Strict matcher lives in _bioguide.StrictBioguideMatcher; we reach it via the
# resolve_with_date helper below to apply trade_date for term-overlap filtering.


def _parse_amount(s: str) -> tuple[int | None, int | None, int | None]:
    s = (s or "").strip()
    if s in AMOUNT_BUCKETS:
        lo, hi = AMOUNT_BUCKETS[s]
        return lo, hi, ((lo + hi) // 2 if hi else lo)
    return None, None, None


def _parse_us_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except Exception:
        return None


def _composite_filing_id(row: dict) -> str:
    """SSW has no DocID. Synthesize a stable hash."""
    sig = "|".join([
        row.get("senator", ""),
        row.get("transaction_date", ""),
        row.get("ticker") or "",
        row.get("type", ""),
        row.get("amount", ""),
    ])
    return "ssw:" + hashlib.sha256(sig.encode()).hexdigest()[:16]


def ingest_senate_stock_watcher(
    engine: Engine,
    *,
    dry_run: bool = False,
) -> IngestResult:
    """Pull the SSW JSON and load all 2014-2019 trades into legislator_trades."""
    log.info("[ssw] starting (dry_run=%s)", dry_run)

    # Resolve our endpoint FK once; every row dict carries this id
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "senate_stock_watcher_historical")

    client = PoliticalHTTPClient(source="senate_stock_watcher", engine=engine)
    body, _path = client.get(URL, save_as=Path("all_transactions.json"), ext="json")
    raw_rows = json.loads(body)
    log.info("[ssw] %d raw rows from mirror", len(raw_rows))

    matcher = StrictBioguideMatcher(engine)
    classifier = AssetClassifier()
    res = IngestResult(rows_attempted=len(raw_rows))

    out_rows: list[dict] = []
    for r in raw_rows:
        tx_date = _parse_us_date(r.get("transaction_date") or "")
        if not tx_date:
            continue
        # SSW lacks filing_date — synthesize as txn + 30d (mid of 0-45-day STOCK Act lag)
        filing_date = tx_date + timedelta(days=30)
        amount = (r.get("amount") or "").strip()
        lo, hi, mid = _parse_amount(amount)
        ticker = (r.get("ticker") or "").strip()
        if ticker in {"--", "", "N/A"}:
            ticker = None
        # SSW has occasional noisy "tickers" (long strings); ticker col is varchar(10)
        if ticker and (len(ticker) > 10 or " " in ticker):
            ticker = None
        # Classify asset_type_code + recover ticker from asset_description if SSW gave none
        asset_desc = r.get("asset_description") or ""
        cls = classifier.classify(asset_desc)
        asset_type_code = cls.asset_type_code
        if not ticker and cls.ticker:
            ticker = cls.ticker
        # STRICT-NULL: don't infer asset_type_code='ST' from a ticker shape alone.
        # Tickers can be ETF/ADR/warrants/foreign — leave NULL on uncertainty.
        senator = (r.get("senator") or "").strip()
        bioguide = matcher.resolve(full_name=senator, chamber="sen", trade_date=tx_date)
        if bioguide:
            res.bioguide_resolved += 1
        else:
            res.bioguide_unresolved += 1

        out_rows.append({
            "country_code": "US",
            "chamber": "senate",
            "bioguide_id": bioguide,
            "legislator_name_raw": senator[:200],
            "endpoint_id": ENDPOINT_ID,
            "filing_id": _composite_filing_id(r),
            "filing_date": filing_date,
            "filing_url": (r.get("ptr_link") or "")[:500] or None,
            "transaction_date": tx_date,
            "notification_date": None,
            # STRICT-NULL: drop fabricated defaults. .get(...) returns None on miss.
            "filer_type": OWNER_MAP_TEXT.get(r.get("owner") or ""),
            "transaction_type": TX_TYPE_MAP_TEXT.get(r.get("type") or ""),
            "asset_name_raw": (r.get("asset_description") or "")[:5000],
            "asset_type_code": asset_type_code,
            "ticker": ticker,
            "amount_str": amount[:40],
            "amount_min": lo,
            "amount_max": hi,
            "amount_mid": mid,
            "raw_archive_id": None,
        })

    log.info("[ssw] prepared %d trade rows (resolved=%d unresolved=%d)",
             len(out_rows), res.bioguide_resolved, res.bioguide_unresolved)

    if dry_run:
        return res

    # Dedup within batch (the SSW historical has ~600 duplicate trade tuples)
    seen: dict[tuple, dict] = {}
    for r in out_rows:
        k = (r["endpoint_id"], r["country_code"], r["chamber"], r["filing_id"],
             r["transaction_date"], r["asset_name_raw"], r["transaction_type"],
             r["amount_str"])
        seen[k] = r
    unique = list(seen.values())
    fast_upsert(
        engine, legislator_trades, unique,
        conflict_keys=["endpoint_id", "country_code", "chamber", "filing_id",
                       "transaction_date", "asset_name_raw", "transaction_type",
                       "amount_str"],
        update_cols=["bioguide_id", "legislator_name_raw", "filing_url",
                     "filer_type", "ticker", "asset_type_code",
                     "amount_min", "amount_max", "amount_mid", "as_of_time"],
    )
    res.trades_inserted = len(unique)
    log.info("[ssw] done: %s", res)
    return res


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from factorlab.storage.db import get_engine
    res = ingest_senate_stock_watcher(get_engine(), dry_run=args.dry_run)
    print(f"\nIngestResult: {res}")
