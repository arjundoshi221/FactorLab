"""USASpending direct → gov_contracts (sovereign primary path).

Strategy: for each ticker, query USASpending using all known recipient names
(parent + subsidiary aliases from `_resolver`). Idempotent — re-running
just rewrites the same `contract_id` rows.

Run:
    python -m factorlab.countries.us.political.usaspending.ingest --tickers LMT,RTX,NOC --fy 2025
    python -m factorlab.countries.us.political.usaspending.ingest --all-known-tickers --fy 2025
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.countries.us.political._client import PoliticalHTTPClient
from factorlab.countries.us.political._db import fast_upsert
from factorlab.countries.us.political._resolver import Resolver
from factorlab.countries.us.political.usaspending.client import iter_contracts_for_recipient
from factorlab.storage.schemas.alt_political_us import contract_aliases, gov_contracts

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    tickers_processed: int = 0
    contracts_inserted: int = 0
    aliases_learned: int = 0


def _to_decimal(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def _s(v, n: int) -> str | None:
    """Coerce any value (int / None / str) to a max-N-char string, or None."""
    if v is None or v == "":
        return None
    return str(v)[:n]


def _to_contract_row(row: dict, *, ticker: str, recipient_query: str, endpoint_id: int) -> dict | None:
    contract_id = _s(row.get("generated_internal_id"), 120)
    if not contract_id:
        return None
    return {
        "contract_id": contract_id,
        "country_code": "US",
        "award_id": _s(row.get("Award ID"), 60),
        "recipient_legal_name": _s(row.get("Recipient Name"), 300) or "",
        "recipient_parent_name": None,
        "recipient_uei": None,
        "recipient_id": _s(row.get("recipient_id"), 50),
        "ticker": _s(ticker, 10),
        "total_value": _to_decimal(row.get("Award Amount")),
        "obligated_amount": None,
        "outlayed_amount": None,
        "potential_amount": None,
        "action_date": _parse_date(row.get("Start Date")),
        "performance_start_date": _parse_date(row.get("Start Date")),
        "performance_end_date": _parse_date(row.get("End Date")),
        "awarding_agency": _s(row.get("Awarding Agency"), 200),
        "awarding_sub_agency": _s(row.get("Awarding Sub Agency"), 200),
        "awarding_office": None,
        "performance_state": _s(row.get("Place of Performance State Code"), 2),
        "performance_country": _s(row.get("Place of Performance Country Code"), 3),
        "performance_district": None,
        "description": row.get("Description"),
        "naics_code": _s(row.get("NAICS"), 10),
        "permalink": None,
        "endpoint_id": endpoint_id,
        "source_recipient_query": _s(recipient_query, 300) or "",
        "raw_archive_id": None,
    }


def _parse_date(s):
    if not s:
        return None
    from datetime import date, datetime
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def _names_for_ticker(resolver: Resolver, ticker: str) -> list[str]:
    """Build list of recipient_search_text candidates for a ticker.

    Sources:
      - SEC canonical name from records list
      - Manual aliases from configs/reference/contractor_aliases.yaml
    """
    ticker = ticker.upper()
    names: list[str] = []
    # SEC canonical
    for r in resolver.records:
        if r.ticker == ticker:
            names.append(r.name)
            break
    # Manual / learned aliases mapping to this ticker
    for norm_name, t in resolver.aliases.items():
        if t == ticker and norm_name not in (n.lower() for n in names):
            names.append(norm_name)
    return names or [ticker]


def ingest_usaspending(
    engine: Engine,
    *,
    tickers: list[str] | None = None,
    fy: int = 2025,
    fy_range: list[int] | None = None,
    dry_run: bool = False,
) -> IngestResult:
    """Pull contracts for the given tickers and FY(s).

    If `tickers` is None, query all distinct tickers from legislator_trades
    (the universe of stocks we've actually seen in PTRs).
    """
    fys = fy_range or [fy]
    log.info("[usaspending] starting tickers=%s fy=%s", tickers or "ALL", fys)

    # Resolve our endpoint FK once; every row dict carries this id
    from factorlab.countries.us.political._db import lookup_endpoint_id
    ENDPOINT_ID = lookup_endpoint_id(engine, "usaspending_direct")

    http = PoliticalHTTPClient(source="usaspending", engine=engine)
    resolver = Resolver()

    if not tickers:
        with engine.connect() as c:
            tickers = [
                r[0] for r in c.execute(text(
                    "SELECT DISTINCT ticker FROM alt_political_us.legislator_trades "
                    "WHERE ticker IS NOT NULL ORDER BY ticker"
                ))
            ]
        log.info("[usaspending] %d unique tickers from legislator_trades", len(tickers))

    res = IngestResult()
    for i, ticker in enumerate(tickers, 1):
        names = _names_for_ticker(resolver, ticker)
        contract_buf: list[dict] = []
        seen: set[str] = set()
        for name in names:
            for fy_year in fys:
                try:
                    for raw in iter_contracts_for_recipient(http, recipient=name, fy=fy_year, max_pages=10):
                        row = _to_contract_row(raw, ticker=ticker, recipient_query=name, endpoint_id=ENDPOINT_ID)
                        if row and row["contract_id"] not in seen:
                            seen.add(row["contract_id"])
                            contract_buf.append(row)
                except Exception as e:
                    log.warning("[usaspending] %s/%s fy=%s fail: %s", ticker, name, fy_year, e)

        if not contract_buf:
            continue
        if not dry_run:
            fast_upsert(
                engine, gov_contracts, contract_buf,
                conflict_keys=["contract_id"],
                update_cols=["country_code", "award_id", "recipient_legal_name",
                             "ticker", "total_value", "action_date",
                             "performance_start_date", "performance_end_date",
                             "awarding_agency", "awarding_sub_agency",
                             "performance_state", "performance_country",
                             "description", "naics_code",
                             "source", "source_recipient_query"],
            )
        res.contracts_inserted += len(contract_buf)
        res.tickers_processed += 1

        # Auto-grow contract_aliases for resolved names
        if not dry_run:
            alias_rows = [
                {"country_code": "US", "normalized_name": n.lower()[:300],
                 "ticker": ticker[:10], "confidence": 1.00, "source": "manual"}
                for n in names
            ]
            fast_upsert(engine, contract_aliases, alias_rows,
                        conflict_keys=["country_code", "normalized_name"],
                        update_cols=["ticker", "confidence", "source"])
            res.aliases_learned += len(alias_rows)

        if i % 25 == 0:
            log.info("[usaspending] %d/%d tickers (contracts so far: %d)",
                     i, len(tickers), res.contracts_inserted)

    log.info("[usaspending] done: %s", res)
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=None, help="Comma-separated tickers, e.g. LMT,RTX")
    ap.add_argument("--fy", type=int, default=2025)
    ap.add_argument("--fy-range", default=None, help="e.g. 2014-2026")
    ap.add_argument("--all-known-tickers", action="store_true",
                    help="Query the union of all tickers in legislator_trades")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.fy_range:
        a, b = args.fy_range.split("-")
        fys = list(range(int(a), int(b) + 1))
    else:
        fys = None

    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]

    from factorlab.storage.db import get_engine
    res = ingest_usaspending(get_engine(), tickers=tickers, fy=args.fy,
                             fy_range=fys, dry_run=args.dry_run)
    print(f"\nIngestResult: {res}")
