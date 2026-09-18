"""FEC ingestion → fec_committees + campaign_donations.

Three discrete stages, runnable independently:

  ingest_fec_committees(engine)
      Pulls (a) every committee linked to our 1,713 legislator_fec_ids
      candidate IDs, plus (b) the corporate-sponsored Qualified-PAC sweep.
      Resolves sponsor_company_ticker via _resolver.Resolver.
      → fec_committees

  ingest_fec_donations_mode_a(engine, cycles)      [build follow-up]
      For each corporate PAC in fec_committees, pull /schedule_a/?contributor_id
      Donations FROM the PAC → recipient committees / candidates.
      → campaign_donations (donor_committee_id IS NOT NULL)

  ingest_fec_donations_mode_b(engine, cycles)      [build follow-up]
      For each member's Principal Campaign Committee, pull
      /schedule_a/?committee_id&min_amount=1000.
      Individual ≥$1K donations to candidate PCCs.
      → campaign_donations (donor_committee_id IS NULL, entity_type='IND')

  ingest_fec(engine, ...)
      Top-level orchestration — calls the three above based on flags.

See docs/data-sources/political/senator-trades.md §1F for the full
architecture, rate-limit considerations, and the candidate_id-filter quirk.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.countries.us.political._client import PoliticalHTTPClient
from factorlab.countries.us.political._db import bulk_insert_ignore, fast_upsert
from factorlab.countries.us.political._resolver import Resolver
from factorlab.countries.us.political._state import State
from factorlab.countries.us.political.fec.client import FECClient
from factorlab.storage.schemas.alt_political_us import (
    campaign_donations,
    fec_committees,
)

log = logging.getLogger(__name__)


# ===========================================================================
#  RESULT DATACLASSES
# ===========================================================================

@dataclass
class CommitteesResult:
    candidate_committees_seen: int = 0
    corporate_pacs_seen: int = 0
    fec_committees_n: int = 0
    tickers_resolved: int = 0
    skipped_no_key: int = 0
    note: str = ""


@dataclass
class DonationsResult:
    pac_committees_processed: int = 0
    candidate_pccs_processed: int = 0
    rows_inserted_mode_a: int = 0
    rows_inserted_mode_b: int = 0
    skipped_no_key: int = 0
    note: str = ""


@dataclass
class IngestResult:
    """Top-level result, mirrors the older stub for orchestrator compat."""
    skipped_no_key: int = 0
    fec_committees_n: int = 0
    campaign_donations_n: int = 0
    note: str = ""
    committees: CommitteesResult | None = None
    donations: DonationsResult | None = None


# ===========================================================================
#  HELPERS
# ===========================================================================

def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def _to_donation_row(r: dict, valid_candidate_ids: set[str] | None = None) -> dict | None:
    """Map a /schedule_a/ row to `campaign_donations` columns.

    Returns None when sub_id is missing (we deduplicate on sub_id; rows
    without it can't be safely upserted without re-creating duplicates on
    re-run). FEC reliably provides sub_id for indexed records.

    `valid_candidate_ids` (optional): if provided, candidate_id values not in
    the set are soft-NULLed. Required for production writes — FEC's Schedule A
    occasionally returns committee-shaped IDs (e.g. C00484535) in the
    candidate_id field, which violate the FK
    `campaign_donations.candidate_id → legislator_fec_ids.fec_candidate_id`.
    """
    sub = (r.get("sub_id") or "").strip()
    if not sub:
        return None
    contributor_id = (r.get("contributor_id") or "").strip()
    raw_cand_id = (r.get("candidate_id") or "")[:9].strip()
    if valid_candidate_ids is not None and raw_cand_id and raw_cand_id not in valid_candidate_ids:
        cand_id = None
        cand_name = None
    else:
        cand_id = raw_cand_id or None
        cand_name = (r.get("candidate_name") or "")[:200] or None
    return {
        "country_code": "US",
        "sub_id": sub[:50],
        "cycle": int(r.get("two_year_transaction_period") or 0),
        "donor_name": (r.get("contributor_name") or "")[:200] or None,
        "donor_employer": (r.get("contributor_employer") or "")[:200] or None,
        "donor_occupation": (r.get("contributor_occupation") or "")[:200] or None,
        "donor_state": (r.get("contributor_state") or "")[:2] or None,
        "donor_zip": (r.get("contributor_zip") or "")[:10] or None,
        "donor_city": (r.get("contributor_city") or "")[:100] or None,
        "donor_committee_country": "US" if contributor_id else None,
        "donor_committee_id": contributor_id[:9] or None,
        "amount": r.get("contribution_receipt_amount"),
        "date": _parse_date(r.get("contribution_receipt_date")),
        "transaction_type": (r.get("receipt_type") or "")[:10] or None,
        "recipient_committee_country": "US" if r.get("committee_id") else None,
        "recipient_committee_id": ((r.get("committee_id") or "")[:9]) or None,
        "recipient_committee_name": (((r.get("committee") or {}).get("name") or "")[:300]) or None,
        "candidate_id": cand_id,
        "candidate_name": cand_name,
        "filing_url": (r.get("pdf_url") or "")[:500] or None,
    }


def _to_committee_row_from_nested(nested: dict, resolver: Resolver) -> dict | None:
    """Build an `fec_committees` row from a Schedule A nested `committee` object.

    Used to lazily populate `fec_committees` for recipient committees we
    encounter via donation queries — avoids FK violations on
    `campaign_donations.recipient_committee_id` without extra API calls.
    """
    if not nested or not nested.get("committee_id"):
        return None
    return _to_committee_row(nested, resolver)


def _stub_donor_committee_from_donation(raw: dict) -> dict | None:
    """Minimal `fec_committees` row from a Schedule A donor's surface fields.

    When the donor is a committee (`entity_type='COM'/'CCM'/'PAC'/'PTY'`),
    Schedule A only exposes `contributor_id` + `contributor_name` — no nested
    object. We synthesize a stub row to satisfy the
    `fk_campaign_donations_donor_committee` FK. Insert-ignore (never update)
    so this stub doesn't overwrite a richer row populated elsewhere.
    """
    contributor_id = (raw.get("contributor_id") or "").strip()
    if not contributor_id:
        return None
    return {
        "country_code": "US",
        "committee_id": contributor_id[:9],
        "name": ((raw.get("contributor_name") or contributor_id) or "")[:300],
        "committee_type": None,
        "committee_type_full": None,
        "designation": None,
        "organization_type": None,
        "sponsor_company_ticker": None,
        "first_file_date": None,
        "last_file_date": None,
    }


def _to_committee_row(c: dict, resolver: Resolver) -> dict:
    """Map a /committees/ response row to fec_committees columns.

    Resolves `sponsor_company_ticker` via the FEC-tuned conservative
    `resolve_pac_name` cascade (no fuzzy, no loose prefix) only when the
    committee looks corporate (organization_type='C' OR committee_type 'Q'/'N').
    """
    name = c.get("name") or ""
    org_type = c.get("organization_type")
    cmt_type = c.get("committee_type")
    ticker = None
    is_corporate_like = (org_type == "C") or (cmt_type in ("Q", "N"))
    if is_corporate_like and name:
        t, _kind, conf = resolver.resolve_pac_name(name)
        if t and conf >= 0.85:    # alias / exact / verified-prefix only
            ticker = t
    return {
        "country_code": "US",
        "committee_id": c["committee_id"],
        "name": name[:300],
        "committee_type": (cmt_type or "")[:2] or None,
        "committee_type_full": (c.get("committee_type_full") or "")[:80] or None,
        "designation": (c.get("designation") or "")[:2] or None,
        "organization_type": (org_type or "")[:2] or None,
        "sponsor_company_ticker": ticker,
        "first_file_date": _parse_date(c.get("first_file_date")),
        "last_file_date": _parse_date(c.get("last_file_date")),
    }


def _key_missing() -> bool:
    """True when the .env key is missing or DEMO."""
    k = (os.getenv("FEC_API_KEY") or "").strip()
    return k in ("", "DEMO_KEY")


# ===========================================================================
#  STAGE 1 — fec_committees
# ===========================================================================

def ingest_fec_committees(
    engine: Engine,
    *,
    candidate_id_limit: int | None = None,
    skip_candidates: bool = False,
    skip_corp_pac_sweep: bool = False,
    dry_run: bool = False,
) -> CommitteesResult:
    """Populate `fec_committees`.

    Source 1: every committee linked to our `legislator_fec_ids.fec_candidate_id`.
              Powers Mode B (need Principal Campaign Committee per member).

    Source 2: corporate-sponsored Qualified PACs via `committee_type=Q&organization_type=C`.
              Powers Mode A.

    Idempotent: re-running adds new committees and refreshes mutable fields.
    Resumable: per-candidate state under data/political/_state/fec_committees.json.
    """
    res = CommitteesResult()
    if _key_missing():
        log.info("[fec] FEC_API_KEY missing — skipping ingest_fec_committees")
        res.skipped_no_key = 1
        res.note = "FEC_API_KEY required; sign up at https://api.open.fec.gov/developers"
        return res

    http = PoliticalHTTPClient(source="fec", engine=engine)
    client = FECClient(http)
    resolver = Resolver()
    state = State(source="fec_committees")

    rows: list[dict] = []
    seen_committee_ids: set[str] = set()

    # ---- Source 1: PCCs for every candidate id we know -----------------
    if skip_candidates:
        cand_ids: list[str] = []
        log.info("[fec] candidate-committees pull SKIPPED (skip_candidates=True)")
    else:
        with engine.connect() as c:
            cand_ids = [
                r[0] for r in c.execute(text("""
                    SELECT DISTINCT fec_candidate_id
                      FROM alt_political_us.legislator_fec_ids
                     ORDER BY fec_candidate_id
                """)).all()
            ]
        if candidate_id_limit is not None:
            cand_ids = cand_ids[:candidate_id_limit]
        log.info("[fec] resolving committees for %d candidate ids", len(cand_ids))

    for i, cand_id in enumerate(cand_ids, 1):
        ckey = f"cand/{cand_id}"
        if state.is_done(ckey):
            continue
        try:
            cmts = client.get_candidate_committees(cand_id)
        except Exception as e:
            log.warning("[fec] candidate %s committees fetch failed: %s", cand_id, e)
            continue
        for cmt in cmts:
            cid = cmt.get("committee_id")
            if not cid or cid in seen_committee_ids:
                continue
            seen_committee_ids.add(cid)
            row = _to_committee_row(cmt, resolver)
            if row["sponsor_company_ticker"]:
                res.tickers_resolved += 1
            rows.append(row)
        res.candidate_committees_seen += 1
        state.mark_done(ckey)
        if i % 100 == 0:
            log.info("[fec] candidate-committees progress: %d/%d  unique_committees=%d",
                     i, len(cand_ids), len(seen_committee_ids))
            state.flush()
    state.flush()

    # ---- Source 2: corp-PAC sweep --------------------------------------
    if not skip_corp_pac_sweep:
        log.info("[fec] sweeping corporate-sponsored Qualified PACs (Q + C)")
        n_seen_before = len(seen_committee_ids)
        for cmt in client.iter_corporate_pacs():
            cid = cmt.get("committee_id")
            if not cid or cid in seen_committee_ids:
                continue
            seen_committee_ids.add(cid)
            row = _to_committee_row(cmt, resolver)
            if row["sponsor_company_ticker"]:
                res.tickers_resolved += 1
            rows.append(row)
        res.corporate_pacs_seen = len(seen_committee_ids) - n_seen_before
        log.info("[fec] corp-PAC sweep added %d committees", res.corporate_pacs_seen)

    res.fec_committees_n = len(rows)

    if dry_run:
        log.info("[fec] DRY-RUN: would upsert %d fec_committees rows "
                 "(of which %d resolved to a ticker)", res.fec_committees_n, res.tickers_resolved)
        return res

    if rows:
        fast_upsert(
            engine, fec_committees, rows,
            conflict_keys=["country_code", "committee_id"],
            update_cols=["name", "committee_type", "committee_type_full",
                         "designation", "organization_type",
                         "sponsor_company_ticker", "first_file_date", "last_file_date"],
        )
    log.info("[fec] committees upserted: %s", res)
    return res


def re_resolve_committees(
    engine: Engine,
    *,
    only_unresolved: bool = False,
    dry_run: bool = False,
) -> dict:
    """Re-run the resolver over existing `fec_committees` rows in-place.

    Use after tuning the resolver / aliases to update `sponsor_company_ticker`
    without re-fetching from FEC. Touches NO API budget.

    `only_unresolved=True` skips rows that already have a ticker — faster but
    won't fix prior false-positive matches. `False` re-evaluates every
    corp-like row.
    """
    resolver = Resolver()
    where = "organization_type='C' OR committee_type IN ('Q','N')"
    if only_unresolved:
        where += " AND sponsor_company_ticker IS NULL"

    with engine.connect() as c:
        rows = c.execute(text(f"""
            SELECT committee_id, name, sponsor_company_ticker
              FROM alt_political_us.fec_committees
             WHERE country_code='US' AND ({where})
        """)).all()

    log.info("[fec] re-resolving %d committee rows (only_unresolved=%s)", len(rows), only_unresolved)

    updates: list[dict] = []
    stats = {"unchanged": 0, "newly_resolved": 0, "ticker_changed": 0, "ticker_cleared": 0}
    by_kind: dict[str, int] = {}

    for committee_id, name, prior in rows:
        new_ticker, kind, conf = resolver.resolve_pac_name(name or "")
        if conf < 0.85:
            new_ticker = None
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if new_ticker == prior:
            stats["unchanged"] += 1
            continue
        if prior is None and new_ticker:
            stats["newly_resolved"] += 1
        elif prior and new_ticker is None:
            stats["ticker_cleared"] += 1
        else:
            stats["ticker_changed"] += 1
        updates.append({
            "country_code": "US",
            "committee_id": committee_id,
            "sponsor_company_ticker": new_ticker,
        })

    log.info("[fec] re-resolve stats: %s  by_kind=%s", stats, by_kind)

    if updates and not dry_run:
        # Patch only the ticker column; leave everything else alone
        with engine.begin() as conn:
            for u in updates:
                conn.execute(text("""
                    UPDATE alt_political_us.fec_committees
                       SET sponsor_company_ticker = :ticker,
                           updated_at = now()
                     WHERE country_code = :cc AND committee_id = :cid
                """), {"ticker": u["sponsor_company_ticker"],
                       "cc": u["country_code"], "cid": u["committee_id"]})
        log.info("[fec] applied %d updates", len(updates))
    return {"stats": stats, "by_kind": by_kind, "rows_touched": len(updates)}


# ===========================================================================
#  STAGE 2 — Mode A donations (corporate PAC → candidates)
# ===========================================================================

DEFAULT_MODE_A_CYCLES = [2014, 2016, 2018, 2020, 2022, 2024, 2026]
DEFAULT_MODE_B_CYCLES = [2018, 2020, 2022, 2024, 2026]
BATCH_SIZE = 5000


def _flush_donations(engine: Engine, buf: list[dict]) -> int:
    """Bulk-insert with conflict-skip on sub_id. Returns rows attempted."""
    if not buf:
        return 0
    bulk_insert_ignore(engine, campaign_donations, buf, conflict_keys=["sub_id"])
    return len(buf)


def _flush_committees(engine: Engine, by_id: dict[str, dict]) -> int:
    """Upsert RICH committees (from /committees/ or nested `committee` object).

    Must run BEFORE `_flush_donations` so the FK constraint
    `fk_campaign_donations_recipient_committee` is satisfied.
    """
    if not by_id:
        return 0
    fast_upsert(
        engine, fec_committees, list(by_id.values()),
        conflict_keys=["country_code", "committee_id"],
        update_cols=["name", "committee_type", "committee_type_full",
                     "designation", "organization_type",
                     "sponsor_company_ticker", "first_file_date", "last_file_date"],
    )
    return len(by_id)


def _flush_committee_stubs(engine: Engine, by_id: dict[str, dict]) -> int:
    """Insert-ignore donor-side stubs (only `committee_id` + `name`).

    Never updates existing rows — a richer row populated by
    `_flush_committees` or the corp-PAC sweep takes precedence.
    """
    if not by_id:
        return 0
    bulk_insert_ignore(
        engine, fec_committees, list(by_id.values()),
        conflict_keys=["country_code", "committee_id"],
    )
    return len(by_id)


def ingest_fec_donations_mode_a(
    engine: Engine,
    *,
    cycles: list[int] | None = None,
    pac_limit: int | None = None,
    include_unresolved_pacs: bool = False,
    dry_run: bool = False,
) -> DonationsResult:
    """Mode A — donations FROM corporate-sponsored PACs.

    For each `fec_committees` row where `organization_type='C'`, pull every
    Schedule A row in `cycles`, writing to `campaign_donations` with
    `donor_committee_id = PAC.committee_id`.

    By default restricts to PACs with a resolved `sponsor_company_ticker`
    (~590 PACs, the alpha-relevant universe). Pass `include_unresolved_pacs=True`
    to also iterate the ~2,730 unresolved corp PACs (private companies, foreign
    parents, defunct entities — most low-volume, mostly noise for the
    sector/ticker signal).

    Idempotent on `sub_id` (campaign_donations unique constraint).
    Resumable via per-(PAC × cycle) checkpoints.
    """
    res = DonationsResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "FEC_API_KEY required"
        return res

    cycles = cycles or DEFAULT_MODE_A_CYCLES

    http = PoliticalHTTPClient(source="fec", engine=engine)
    client = FECClient(http)
    resolver = Resolver()
    state = State(source="fec_donations_mode_a")

    # Source the corporate PAC universe from fec_committees
    where = "country_code = 'US' AND organization_type = 'C'"
    if not include_unresolved_pacs:
        where += " AND sponsor_company_ticker IS NOT NULL"
    with engine.connect() as c:
        pacs = [
            (r[0], r[1]) for r in c.execute(text(f"""
                SELECT committee_id, name
                  FROM alt_political_us.fec_committees
                 WHERE {where}
                 ORDER BY committee_id
            """)).all()
    ]
        # Pre-load valid candidate_ids for FK soft-NULL filter on
        # campaign_donations.candidate_id → legislator_fec_ids.fec_candidate_id.
        # FEC's Schedule A occasionally returns committee-shaped IDs (e.g.
        # C00484535) in candidate_id; passing those through violates the FK.
        valid_candidate_ids = {
            r[0] for r in c.execute(text(
                "SELECT fec_candidate_id FROM alt_political_us.legislator_fec_ids"
            ))
        }
    if pac_limit:
        pacs = pacs[:pac_limit]
    log.info("[fec] Mode A: %d corporate PACs (%s) × %d cycles  (FK-filter pool: %d candidate ids)",
             len(pacs),
             "ticker-resolved only" if not include_unresolved_pacs else "ALL incl. unresolved",
             len(cycles), len(valid_candidate_ids))

    rows_buffer: list[dict] = []
    new_committees: dict[str, dict] = {}        # rich (from nested object)
    stub_committees: dict[str, dict] = {}       # minimal (from contributor_id)
    null_sub_skipped = 0

    for pac_idx, (pac_id, pac_name) in enumerate(pacs, 1):
        for cycle in cycles:
            ckey = f"pac/{pac_id}/cycle/{cycle}"
            if state.is_done(ckey):
                continue
            try:
                for raw in client.iter_schedule_a(
                    contributor_id=pac_id,
                    two_year_transaction_period=cycle,
                ):
                    row = _to_donation_row(raw, valid_candidate_ids)
                    if row is None:
                        null_sub_skipped += 1
                        continue
                    # Lazy-populate fec_committees for the recipient (rich)
                    cmt = _to_committee_row_from_nested(raw.get("committee") or {}, resolver)
                    if cmt and cmt["committee_id"] not in new_committees:
                        new_committees[cmt["committee_id"]] = cmt
                    # Stub the donor committee if we have a contributor_id (rare in Mode A — donor IS pac_id, already known)
                    stub = _stub_donor_committee_from_donation(raw)
                    if stub and stub["committee_id"] not in stub_committees:
                        stub_committees[stub["committee_id"]] = stub
                    rows_buffer.append(row)
                    if len(rows_buffer) >= BATCH_SIZE:
                        if not dry_run:
                            _flush_committees(engine, new_committees)
                            _flush_committee_stubs(engine, stub_committees)
                            new_committees = {}
                            stub_committees = {}
                            res.rows_inserted_mode_a += _flush_donations(engine, rows_buffer)
                        rows_buffer = []
            except Exception as e:
                log.warning("[fec] Mode A pac=%s cycle=%d failed: %s", pac_id, cycle, e)
                continue
            state.mark_done(ckey)
        res.pac_committees_processed += 1
        if pac_idx % 25 == 0:
            state.flush()
            log.info("[fec] Mode A progress: %d/%d PACs  inserted=%d  null_sub_skipped=%d",
                     pac_idx, len(pacs), res.rows_inserted_mode_a, null_sub_skipped)

    if not dry_run:
        _flush_committees(engine, new_committees)
        _flush_committee_stubs(engine, stub_committees)
        if rows_buffer:
            res.rows_inserted_mode_a += _flush_donations(engine, rows_buffer)
    state.flush()

    if null_sub_skipped:
        res.note = f"skipped {null_sub_skipped} rows with NULL sub_id"
    log.info("[fec] Mode A done: %s", res)
    return res


# ===========================================================================
#  STAGE 3 — Mode B donations (individuals ≥$1K → candidate PCCs)
# ===========================================================================

def ingest_fec_donations_mode_b(
    engine: Engine,
    *,
    cycles: list[int] | None = None,
    candidate_limit: int | None = None,
    dry_run: bool = False,
) -> DonationsResult:
    """Mode B — individual ≥$1K donations TO candidate Principal Campaign Committees.

    For each `fec_committees` row where `designation='P'` (PCC), pull every
    Schedule A row in `cycles` filtered by `min_amount=1000`. Writes to
    `campaign_donations` with `donor_committee_id` typically NULL (rows are
    individual-sourced; entity_type='IND' on the API side).
    """
    res = DonationsResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "FEC_API_KEY required"
        return res

    cycles = cycles or DEFAULT_MODE_B_CYCLES

    http = PoliticalHTTPClient(source="fec", engine=engine)
    client = FECClient(http)
    resolver = Resolver()
    state = State(source="fec_donations_mode_b")

    with engine.connect() as c:
        pccs = [
            (r[0], r[1]) for r in c.execute(text("""
                SELECT committee_id, name
                  FROM alt_political_us.fec_committees
                 WHERE country_code = 'US'
                   AND designation = 'P'
                 ORDER BY committee_id
            """)).all()
    ]
        # FK soft-NULL filter for campaign_donations.candidate_id (see Mode A)
        valid_candidate_ids = {
            r[0] for r in c.execute(text(
                "SELECT fec_candidate_id FROM alt_political_us.legislator_fec_ids"
            ))
        }
    if candidate_limit:
        pccs = pccs[:candidate_limit]
    log.info("[fec] Mode B: %d PCCs × %d cycles  (FK-filter pool: %d candidate ids)",
             len(pccs), len(cycles), len(valid_candidate_ids))

    rows_buffer: list[dict] = []
    new_committees: dict[str, dict] = {}        # rich (from nested object) — recipient PCC
    stub_committees: dict[str, dict] = {}       # minimal — donor side committees (joint funding, party, PACs)
    null_sub_skipped = 0

    for pcc_idx, (pcc_id, pcc_name) in enumerate(pccs, 1):
        for cycle in cycles:
            ckey = f"pcc/{pcc_id}/cycle/{cycle}"
            if state.is_done(ckey):
                continue
            try:
                for raw in client.iter_schedule_a(
                    committee_id=pcc_id,
                    two_year_transaction_period=cycle,
                    min_amount=1000,
                ):
                    row = _to_donation_row(raw, valid_candidate_ids)
                    if row is None:
                        null_sub_skipped += 1
                        continue
                    # Recipient (the PCC itself; almost always already in fec_committees, but cheap to upsert)
                    cmt = _to_committee_row_from_nested(raw.get("committee") or {}, resolver)
                    if cmt and cmt["committee_id"] not in new_committees:
                        new_committees[cmt["committee_id"]] = cmt
                    # Donor side — most rows are individuals (no contributor_id) but committee donors need a stub
                    stub = _stub_donor_committee_from_donation(raw)
                    if stub and stub["committee_id"] not in stub_committees:
                        stub_committees[stub["committee_id"]] = stub
                    rows_buffer.append(row)
                    if len(rows_buffer) >= BATCH_SIZE:
                        if not dry_run:
                            _flush_committees(engine, new_committees)
                            _flush_committee_stubs(engine, stub_committees)
                            new_committees = {}
                            stub_committees = {}
                            res.rows_inserted_mode_b += _flush_donations(engine, rows_buffer)
                        rows_buffer = []
            except Exception as e:
                log.warning("[fec] Mode B pcc=%s cycle=%d failed: %s", pcc_id, cycle, e)
                continue
            state.mark_done(ckey)
        res.candidate_pccs_processed += 1
        if pcc_idx % 50 == 0:
            state.flush()
            log.info("[fec] Mode B progress: %d/%d PCCs  inserted=%d  null_sub_skipped=%d",
                     pcc_idx, len(pccs), res.rows_inserted_mode_b, null_sub_skipped)

    if not dry_run:
        _flush_committees(engine, new_committees)
        _flush_committee_stubs(engine, stub_committees)
        if rows_buffer:
            res.rows_inserted_mode_b += _flush_donations(engine, rows_buffer)
    state.flush()

    if null_sub_skipped:
        res.note = f"skipped {null_sub_skipped} rows with NULL sub_id"
    log.info("[fec] Mode B done: %s", res)
    return res


# ===========================================================================
#  TOP-LEVEL — orchestrator-compatible entrypoint
# ===========================================================================

def ingest_fec(
    engine: Engine,
    *,
    do_committees: bool = True,
    do_mode_a: bool = False,
    do_mode_b: bool = False,
    cycles: list[int] | None = None,
    candidate_id_limit: int | None = None,
    skip_candidates: bool = False,
    skip_corp_pac_sweep: bool = False,
    include_unresolved_pacs: bool = False,
    dry_run: bool = False,
    **_kw,
) -> IngestResult:
    """Top-level FEC ingestion. Defaults to committees-only (Phase 3 boot).
    Pass `do_mode_a=True, do_mode_b=True` after committees lands to trigger
    full donations backfill.
    """
    res = IngestResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "FEC_API_KEY required; sign up at https://api.open.fec.gov/developers"
        log.info("[fec] %s", res.note)
        return res

    if do_committees:
        cr = ingest_fec_committees(
            engine,
            candidate_id_limit=candidate_id_limit,
            skip_candidates=skip_candidates,
            skip_corp_pac_sweep=skip_corp_pac_sweep,
            dry_run=dry_run,
        )
        res.committees = cr
        res.fec_committees_n = cr.fec_committees_n

    if do_mode_a:
        a = ingest_fec_donations_mode_a(
            engine, cycles=cycles,
            include_unresolved_pacs=include_unresolved_pacs,
            dry_run=dry_run,
        )
        res.donations = a
        res.campaign_donations_n += a.rows_inserted_mode_a

    if do_mode_b:
        b = ingest_fec_donations_mode_b(engine, cycles=cycles, dry_run=dry_run)
        if res.donations is None:
            res.donations = b
        else:
            res.donations.rows_inserted_mode_b += b.rows_inserted_mode_b
            res.donations.candidate_pccs_processed += b.candidate_pccs_processed
        res.campaign_donations_n += b.rows_inserted_mode_b

    return res


# ===========================================================================
#  CLI
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--committees", action="store_true",
                    help="Run Stage 1 (fec_committees population)")
    ap.add_argument("--mode-a", action="store_true", help="Run Mode A donations")
    ap.add_argument("--mode-b", action="store_true", help="Run Mode B donations")
    ap.add_argument("--re-resolve", action="store_true",
                    help="Re-run resolver over existing fec_committees rows (no API calls)")
    ap.add_argument("--re-resolve-only-unresolved", action="store_true",
                    help="With --re-resolve: skip rows that already have a ticker")
    ap.add_argument("--include-unresolved-pacs", action="store_true",
                    help="Mode A: also iterate corp PACs with no resolved ticker (default: skip)")
    ap.add_argument("--candidate-limit", type=int, default=None,
                    help="Cap candidate ids processed (testing)")
    ap.add_argument("--skip-candidates", action="store_true",
                    help="Skip the candidate-committees pull entirely (testing corp-PAC path)")
    ap.add_argument("--skip-corp-pac-sweep", action="store_true",
                    help="Skip the broad corp-PAC sweep (testing)")
    ap.add_argument("--cycles", default=None,
                    help="Comma-separated cycles for donations modes, e.g. 2022,2024")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cycles = [int(x) for x in args.cycles.split(",")] if args.cycles else None

    from factorlab.storage.db import get_engine

    if args.re_resolve:
        out = re_resolve_committees(
            get_engine(),
            only_unresolved=args.re_resolve_only_unresolved,
            dry_run=args.dry_run,
        )
        print(f"\nRe-resolve summary: {out}")
        sys.exit(0)

    do_committees = args.committees or not (args.mode_a or args.mode_b)

    res = ingest_fec(
        get_engine(),
        do_committees=do_committees,
        do_mode_a=args.mode_a,
        do_mode_b=args.mode_b,
        cycles=cycles,
        candidate_id_limit=args.candidate_limit,
        skip_candidates=args.skip_candidates,
        skip_corp_pac_sweep=args.skip_corp_pac_sweep,
        include_unresolved_pacs=args.include_unresolved_pacs,
        dry_run=args.dry_run,
    )
    print(f"\nIngestResult: {res}")
