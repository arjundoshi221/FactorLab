"""LDA filings → 4 alt_political_us tables.

Walks paginated /filings/, resolves client_name → ticker via
`_resolver.Resolver`, upserts:
    lobbying_filings
    lobbying_activities
    lobbying_activity_targets
    lobbying_activity_lobbyists
    lobby_client_aliases  (auto-grows on each new resolution)

Run:
    python -m factorlab.countries.us.political.lda.ingest --year 2024
    python -m factorlab.countries.us.political.lda.ingest --year-range 2014-2026
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.engine import Engine

from factorlab.countries.us.political._client import PoliticalHTTPClient
from factorlab.countries.us.political._db import bulk_insert_ignore, fast_upsert
from factorlab.countries.us.political._resolver import Resolver, normalize_name
from factorlab.countries.us.political.lda.client import LDAClient
from factorlab.storage.schemas.alt_political_us import (
    lobby_client_aliases,
    lobbying_activities,
    lobbying_activity_lobbyists,
    lobbying_activity_targets,
    lobbying_filings,
)

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    filings_n: int = 0
    activities_n: int = 0
    targets_n: int = 0
    lobbyists_n: int = 0
    client_aliases_learned: int = 0
    tickers_resolved: int = 0
    tickers_unresolved: int = 0


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _to_filing_row(f: dict, client_ticker: str | None) -> dict:
    return {
        "filing_uuid": UUID(f["filing_uuid"]),
        "country_code": "US",
        "filing_type": (f.get("filing_type") or "")[:4],
        "filing_year": f.get("filing_year"),
        "filing_period": (f.get("filing_period") or "")[:20] or None,
        "client_name": ((f.get("client") or {}).get("name") or "")[:300],
        "client_id": (f.get("client") or {}).get("id"),
        "client_ticker": client_ticker,
        "registrant_name": ((f.get("registrant") or {}).get("name") or "")[:300],
        "registrant_id": (f.get("registrant") or {}).get("id"),
        "income": _to_decimal(f.get("income")),
        "expenses": _to_decimal(f.get("expenses")),
        "dt_posted": _parse_dt(f.get("dt_posted")),
        "filing_document_url": (f.get("filing_document_url") or "")[:500] or None,
        "termination_date": _parse_date(f.get("termination_date")),
        "raw_archive_id": None,
    }


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
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def _expand_activities(filing_uuid: UUID, activities: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Flatten lobbying_activities + targets + lobbyists from one filing."""
    act_rows: list[dict] = []
    tgt_rows: list[dict] = []
    lob_rows: list[dict] = []
    for a in (activities or []):
        activity_id = uuid4()
        issue_code = a.get("general_issue_code")
        if issue_code and len(issue_code) > 3:
            issue_code = None
        act_rows.append({
            "activity_id": activity_id,
            "filing_uuid": filing_uuid,
            "country_code": "US",
            "issue_code": issue_code,
            "description": a.get("description"),
            "foreign_entity_issues": a.get("foreign_entity_issues"),
        })
        for ent in (a.get("government_entities") or []):
            if ent.get("id") is None:
                continue
            tgt_rows.append({
                "activity_id": activity_id,
                "country_code": "US",
                "entity_id": ent["id"],
            })
        for lb in (a.get("lobbyists") or []):
            person = lb.get("lobbyist") or {}
            lob_rows.append({
                "activity_id": activity_id,
                "lobbyist_id": person.get("id") or 0,
                "last_name": (person.get("last_name") or "")[:80],
                "first_name": (person.get("first_name") or "")[:80],
                "middle_name": (person.get("middle_name") or "")[:80] or None,
                "suffix": (person.get("suffix") or "")[:20] or None,
                "covered_position": lb.get("covered_position"),
                "is_new": bool(lb.get("new")),
            })
    return act_rows, tgt_rows, lob_rows


def ingest_lda(
    engine: Engine,
    *,
    years: list[int] | None = None,
    page_size: int = 100,
    max_pages_per_year: int | None = None,
    dry_run: bool = False,
) -> IngestResult:
    """Backfill LDA filings + activities + targets + lobbyists.

    Default: pull every filing for every year in `years` (or 2014-2026).
    """
    years = years or list(range(2014, 2027))
    log.info("[lda] starting years=%s page_size=%d max_pages_per_year=%s",
             years, page_size, max_pages_per_year)

    http = PoliticalHTTPClient(source="lda", engine=engine)
    client = LDAClient(http)
    resolver = Resolver()
    res = IngestResult()

    # Track learned aliases to upsert at end
    learned_aliases: dict[str, str] = {}

    for year in years:
        filing_buf: list[dict] = []
        act_buf: list[dict] = []
        tgt_buf: list[dict] = []
        lob_buf: list[dict] = []

        log.info("[lda] year %s: pulling filings", year)
        for f in client.iter_filings(filing_year=year, page_size=page_size,
                                     max_pages=max_pages_per_year):
            client_name = (f.get("client") or {}).get("name") or ""
            ticker, kind, conf = resolver.resolve(client_name)
            if ticker:
                res.tickers_resolved += 1
                if kind in {"exact", "prefix", "fuzzy"} and conf >= 0.7:
                    learned_aliases[normalize_name(client_name)] = ticker
            else:
                res.tickers_unresolved += 1

            try:
                fuuid = UUID(f["filing_uuid"])
            except Exception:
                continue
            filing_buf.append(_to_filing_row(f, ticker))
            a, t, l = _expand_activities(fuuid, f.get("lobbying_activities") or [])
            act_buf.extend(a)
            tgt_buf.extend(t)
            lob_buf.extend(l)

        log.info("[lda] year %s: filings=%d activities=%d targets=%d lobbyists=%d",
                 year, len(filing_buf), len(act_buf), len(tgt_buf), len(lob_buf))

        if dry_run:
            res.filings_n += len(filing_buf)
            res.activities_n += len(act_buf)
            res.targets_n += len(tgt_buf)
            res.lobbyists_n += len(lob_buf)
            continue

        # Order matters: filings → activities → (targets, lobbyists)
        if filing_buf:
            # Dedup by filing_uuid — LDA pagination orders by dt_posted, so
            # filings posted mid-paging can shift offsets and reappear on a
            # later page. ON CONFLICT DO UPDATE rejects same-row duplicates
            # in one statement, so collapse to last-seen here.
            by_uuid: dict = {}
            for row in filing_buf:
                by_uuid[row["filing_uuid"]] = row
            filing_buf_dedup = list(by_uuid.values())
            fast_upsert(
                engine, lobbying_filings, filing_buf_dedup,
                conflict_keys=["filing_uuid"],
                update_cols=["country_code", "filing_type", "filing_year",
                             "filing_period", "client_name", "client_id",
                             "client_ticker", "registrant_name", "registrant_id",
                             "income", "expenses", "dt_posted",
                             "filing_document_url", "termination_date"],
            )
            res.filings_n += len(filing_buf_dedup)
        if act_buf:
            bulk_insert_ignore(engine, lobbying_activities, act_buf,
                               conflict_keys=["activity_id"])
            res.activities_n += len(act_buf)
        if tgt_buf:
            # Dedup defensively
            seen = set()
            unique = []
            for t in tgt_buf:
                k = (t["activity_id"], t["country_code"], t["entity_id"])
                if k not in seen:
                    seen.add(k)
                    unique.append(t)
            bulk_insert_ignore(engine, lobbying_activity_targets, unique,
                               conflict_keys=["activity_id", "country_code", "entity_id"])
            res.targets_n += len(unique)
        if lob_buf:
            seen = set()
            unique = []
            for l in lob_buf:
                k = (l["activity_id"], l["lobbyist_id"], l["last_name"], l["first_name"])
                if k not in seen:
                    seen.add(k)
                    unique.append(l)
            bulk_insert_ignore(
                engine, lobbying_activity_lobbyists, unique,
                conflict_keys=["activity_id", "lobbyist_id", "last_name", "first_name"],
            )
            res.lobbyists_n += len(unique)

    # Persist learned aliases
    if learned_aliases and not dry_run:
        alias_rows = [
            {
                "country_code": "US",
                "normalized_name": k[:300],
                "ticker": v[:10],
                "confidence": 0.85,
                "source": "learned",
            }
            for k, v in learned_aliases.items()
        ]
        fast_upsert(
            engine, lobby_client_aliases, alias_rows,
            conflict_keys=["country_code", "normalized_name"],
            update_cols=["ticker", "confidence", "source"],
        )
        res.client_aliases_learned = len(alias_rows)

    log.info("[lda] done: %s", res)
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--year-range", default=None, help="e.g. 2014-2026")
    ap.add_argument("--max-pages-per-year", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.year_range:
        a, b = args.year_range.split("-")
        years = list(range(int(a), int(b) + 1))
    elif args.year is not None:
        years = [args.year]
    else:
        years = None

    from factorlab.storage.db import get_engine
    res = ingest_lda(get_engine(), years=years,
                     max_pages_per_year=args.max_pages_per_year,
                     dry_run=args.dry_run)
    print(f"\nIngestResult: {res}")
