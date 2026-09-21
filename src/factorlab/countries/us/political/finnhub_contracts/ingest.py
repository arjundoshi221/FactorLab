"""Finnhub /stock/usa-spending → gov_contracts (source='finnhub_usa_spending').

Free-tier endpoint. ~60 req/min. Finnhub does parent-corp → ticker mapping
for us, so we don't need a resolver. Coexists with USASpending direct path.

Run:
    python -m factorlab.countries.us.political.finnhub_contracts.ingest --tickers LMT,RTX --fy 2025
    python -m factorlab.countries.us.political.finnhub_contracts.ingest --all-known-tickers --fy 2025
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.countries.us.political._client import PoliticalHTTPClient
from factorlab.countries.us.political._db import fast_upsert
from factorlab.storage.schemas.alt_political_us import gov_contracts

load_dotenv(find_dotenv(usecwd=True))
log = logging.getLogger(__name__)

URL = "https://finnhub.io/api/v1/stock/usa-spending"


@dataclass
class IngestResult:
    tickers_processed: int = 0
    contracts_inserted: int = 0
    skipped_no_key: int = 0


def _to_decimal(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_date(s):
    if not s:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def _s(v, n: int) -> str | None:
    if v is None or v == "":
        return None
    return str(v)[:n]


def _to_contract_row(row: dict, *, ticker: str, endpoint_id: int) -> dict | None:
    permalink = row.get("permalink") or ""
    # Synthesize a stable contract_id namespaced to Finnhub
    award_part = permalink.rstrip("/").rsplit("/", 1)[-1] if permalink else None
    if not award_part:
        # Fallback: hash of recipient + actionDate + total
        import hashlib
        sig = f"{ticker}|{row.get('recipientName')}|{row.get('actionDate')}|{row.get('totalValue')}"
        award_part = hashlib.sha256(sig.encode()).hexdigest()[:24]
    contract_id = f"finnhub:{award_part}"[:120]

    return {
        "contract_id": contract_id,
        "country_code": "US",
        "award_id": None,
        "recipient_legal_name": _s(row.get("recipientName"), 300) or "",
        "recipient_parent_name": _s(row.get("recipientParentName"), 300),
        "recipient_uei": None,
        "recipient_id": None,
        "ticker": _s(ticker, 10),
        "total_value": _to_decimal(row.get("totalValue")),
        "obligated_amount": _to_decimal(row.get("obligatedAmount")),
        "outlayed_amount": _to_decimal(row.get("outlayedAmount")),
        "potential_amount": _to_decimal(row.get("potentialAmount")),
        "action_date": _parse_date(row.get("actionDate")),
        "performance_start_date": _parse_date(row.get("performanceStartDate")),
        "performance_end_date": _parse_date(row.get("performanceEndDate")),
        "awarding_agency": _s(row.get("awardingAgencyName"), 200),
        "awarding_sub_agency": _s(row.get("awardingSubAgencyName"), 200),
        "awarding_office": _s(row.get("awardingOfficeName"), 200),
        "performance_state": _s(row.get("performanceState"), 2),
        "performance_country": _s(row.get("performanceCountry"), 3),
        "performance_district": _s(row.get("performanceCongressionalDistrict"), 8),
        "description": row.get("awardDescription"),
        "naics_code": _s(row.get("naicsCode"), 10),
        "permalink": _s(permalink, 500),
        "endpoint_id": endpoint_id,
        "source_recipient_query": _s(ticker, 300),
        "raw_archive_id": None,
        "last_modified_date": _parse_date(row.get("lastModifiedDate")),
    }


def ingest_finnhub_contracts(
    engine: Engine,
    *,
    tickers: list[str] | None = None,
    fy: int = 2025,
    fy_range: list[int] | None = None,
    rate_limit_rpm: int = 55,
    dry_run: bool = False,
) -> IngestResult:
    fys = fy_range or [fy]
    res = IngestResult()

    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        log.warning("[finnhub] FINNHUB_API_KEY missing — skipping")
        res.skipped_no_key = 1
        return res

    log.info("[finnhub] starting tickers=%s fys=%s", tickers or "ALL", fys)
    http = PoliticalHTTPClient(source="finnhub", engine=engine)

    # Resolve our endpoint FK once; every row dict carries this id
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "finnhub_usa_spending")

    if not tickers:
        with engine.connect() as c:
            tickers = [r[0] for r in c.execute(text(
                "SELECT DISTINCT ticker FROM alt_political_us.legislator_trades "
                "WHERE ticker IS NOT NULL ORDER BY ticker"
            ))]
        log.info("[finnhub] %d unique tickers", len(tickers))

    sleep_per_call = 60.0 / max(rate_limit_rpm, 1)

    for i, ticker in enumerate(tickers, 1):
        contract_buf: list[dict] = []
        for fy_year in fys:
            params = {
                "symbol": ticker,
                "from": f"{fy_year - 1}-10-01",
                "to":   f"{fy_year}-09-30",
                "token": api_key,
            }
            save_as = Path("usa_spending") / f"{ticker}_FY{fy_year}.json"
            try:
                body, _ = http.get(URL, params=params, save_as=save_as, ext="json")
                # Finnhub paid endpoints return HTML (paywall)
                if body.strip().startswith(b"<"):
                    log.warning("[finnhub] %s FY%s returned HTML (paywall?)", ticker, fy_year)
                    continue
                data = json.loads(body)
                rows = data if isinstance(data, list) else (data.get("data") or [])
            except Exception as e:
                log.warning("[finnhub] %s FY%s fetch fail: %s", ticker, fy_year, e)
                continue

            for raw in rows:
                row = _to_contract_row(raw, ticker=ticker, endpoint_id=ENDPOINT_ID)
                if row:
                    contract_buf.append(row)

            time.sleep(sleep_per_call)

        if not contract_buf:
            continue
        # Dedup by contract_id (Finnhub can return multiple rows that synthesize
        # to the same key when the same award has multiple actions in window)
        seen: dict[str, dict] = {}
        for row in contract_buf:
            seen[row["contract_id"]] = row
        contract_buf = list(seen.values())

        if not dry_run:
            fast_upsert(
                engine, gov_contracts, contract_buf,
                conflict_keys=["contract_id"],
                update_cols=["country_code", "recipient_legal_name", "recipient_parent_name",
                             "ticker", "total_value", "obligated_amount", "outlayed_amount",
                             "potential_amount", "action_date", "performance_start_date",
                             "performance_end_date", "awarding_agency", "awarding_sub_agency",
                             "awarding_office", "performance_state", "performance_country",
                             "performance_district", "description", "naics_code",
                             "permalink", "last_modified_date", "source"],
            )
        res.contracts_inserted += len(contract_buf)
        res.tickers_processed += 1

        if i % 25 == 0:
            log.info("[finnhub] %d/%d tickers done (contracts so far: %d)",
                     i, len(tickers), res.contracts_inserted)

    log.info("[finnhub] done: %s", res)
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--fy", type=int, default=2025)
    ap.add_argument("--fy-range", default=None)
    ap.add_argument("--all-known-tickers", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    fys = None
    if args.fy_range:
        a, b = args.fy_range.split("-")
        fys = list(range(int(a), int(b) + 1))
    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]

    from factorlab.storage.db import get_engine
    res = ingest_finnhub_contracts(get_engine(), tickers=tickers, fy=args.fy,
                                   fy_range=fys, dry_run=args.dry_run)
    print(f"\nIngestResult: {res}")
