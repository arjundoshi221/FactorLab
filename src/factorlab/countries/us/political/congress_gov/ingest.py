"""Congress.gov ingestion → bills + bill_sponsors + bill_committees +
bill_actions + hearings.

Three-pass design for bills (cost-shaping — see docs §1C):

  Pass A — list pull (~210 calls / 3 congresses)
      One sparse skeleton row per bill into `bills`.

  Pass B — detail enrichment (~52,576 calls × 3 congresses)
      Adds `policy_area`, `introduced_date`. Inserts the primary sponsor row.

  Pass C — deep fetch (priority-policy bills only, ~63,000 calls)
      `cosponsors`, `committees`, `actions` sub-resources.

Hearings are simpler: list → detail. No witnesses field exposed by the v3 API,
so `alt_political_us.hearing_witnesses` stays empty (schema preserved for a future
transcript-parser pass).

Resumable via per-pass `_state.State` checkpoints; idempotent on
PK-conflict-skip.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.countries.us.political._client import PoliticalHTTPClient
from factorlab.countries.us.political._db import bulk_insert_ignore, fast_upsert
from factorlab.countries.us.political._state import State
from factorlab.countries.us.political.congress_gov.client import CongressGovClient
from factorlab.storage.schemas.alt_political_us import (
    bill_actions,
    bill_committees,
    bill_sponsors,
    bills,
    hearings,
)

log = logging.getLogger(__name__)


# Priority policyArea filter — bills passing this set get Pass C deep-fetch.
# Source: docs/data-sources/political/senator-trades.md §1C.
PRIORITY_POLICY_AREAS: frozenset[str] = frozenset({
    "Armed Forces and National Security",
    "Health",
    "Finance and Financial Sector",
    "Energy",
    "Science, Technology, Communications",
    "Taxation",
    "Foreign Trade and International Finance",
    "Transportation and Public Works",
    "Environmental Protection",
    "Commerce",
    "Public Lands and Natural Resources",
    "Agriculture and Food",
    "Labor and Employment",
    "Housing and Community Development",
    "Economics and Public Finance",
})

DEFAULT_CONGRESSES = [117, 118, 119]
BATCH_SIZE = 2000

# Fixed namespace UUID for deterministic `bill_actions.action_id` generation.
# action_id = uuid5(NAMESPACE_BILL_ACTIONS, f"{bill_uid}|{action_date}|{action_text}")
# Same input always produces the same UUID → idempotent inserts on re-run
# without needing a DB unique-constraint migration. Generated once via
# `uuid.uuid4()`; do NOT change without dropping bill_actions and re-running
# Pass C (changing the namespace would re-generate every action_id, doubling
# the table on conflict-skip-by-action_id semantics).
NAMESPACE_BILL_ACTIONS = uuid.UUID("8c4f1a3e-7b2d-4f5c-9e1a-3d7b4f5c9e1a")


# ===========================================================================
#  RESULT DATACLASSES
# ===========================================================================

@dataclass
class BillsResult:
    list_passes: int = 0
    bills_listed: int = 0
    detail_passes: int = 0
    bills_enriched: int = 0
    primary_sponsors_inserted: int = 0
    deep_fetch_passes: int = 0
    cosponsors_inserted: int = 0
    committees_inserted: int = 0
    actions_inserted: int = 0
    bills_skipped_non_priority: int = 0
    skipped_no_key: int = 0
    note: str = ""


@dataclass
class HearingsResult:
    hearings_listed: int = 0
    hearings_detailed: int = 0
    hearings_inserted: int = 0
    skipped_no_key: int = 0
    note: str = ""


@dataclass
class IngestResult:
    skipped_no_key: int = 0
    bills: BillsResult | None = None
    hearings_res: HearingsResult | None = None
    note: str = ""


# ===========================================================================
#  HELPERS
# ===========================================================================

def _key_missing() -> bool:
    k = (os.getenv("CONGRESS_API_KEY") or "").strip()
    return k in ("", "DEMO_KEY")


def _bill_uid(congress: int, bill_type: str, number: int | str) -> str:
    """'US-119-HR-1' style canonical bill key."""
    return f"US-{int(congress)}-{bill_type.upper()}-{int(number)}"


def _normalize_committee_id(system_code: str | None) -> str | None:
    """Map Congress.gov `systemCode` (lowercase, '00'-suffixed) to our Thomas
    ID convention: 4-char for full committees, 6-char for subcommittees.

    'hswm00' → 'HSWM'    (full Ways & Means)
    'hwsm05' → 'HWSM05'  (subcommittee 05 — kept as-is, uppercased)
    """
    if not system_code:
        return None
    s = system_code.strip().upper()
    if not s:
        return None
    # Trailing '00' is the "full committee" marker — strip to match our 4-char IDs
    if len(s) == 6 and s.endswith("00"):
        return s[:4]
    return s


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _to_bill_skeleton_row(b: dict) -> dict | None:
    """Map a list-mode bill payload to `alt_political_us.bills` skeleton row.
    No policy_area or introduced_date — those land in Pass B.
    """
    cong = b.get("congress")
    btype = (b.get("type") or "").upper()
    num = b.get("number")
    if cong is None or not btype or num is None:
        return None
    try:
        num_int = int(num)
    except (TypeError, ValueError):
        return None
    la = b.get("latestAction") or {}
    return {
        "bill_uid": _bill_uid(cong, btype, num_int),
        "country_code": "US",
        "congress": int(cong),
        "bill_type": btype[:10],
        "bill_number": num_int,
        "origin_chamber": (b.get("originChamber") or "")[:8] or None,
        "title": b.get("title"),
        "policy_area": None,
        "introduced_date": None,
        "latest_action_date": _parse_date(la.get("actionDate")),
        "latest_action_text": la.get("text"),
        "update_date": _parse_dt(b.get("updateDate") or b.get("updateDateIncludingText")),
        "url": (b.get("url") or "")[:300] or None,
    }


def _enrich_bill_from_detail(b: dict) -> dict | None:
    """Map a detail payload to `bills` columns (full update including policy_area)."""
    cong = b.get("congress")
    btype = (b.get("type") or "").upper()
    num = b.get("number")
    if cong is None or not btype or num is None:
        return None
    try:
        num_int = int(num)
    except (TypeError, ValueError):
        return None
    la = b.get("latestAction") or {}
    pa = (b.get("policyArea") or {}).get("name")
    return {
        "bill_uid": _bill_uid(cong, btype, num_int),
        "country_code": "US",
        "congress": int(cong),
        "bill_type": btype[:10],
        "bill_number": num_int,
        "origin_chamber": (b.get("originChamber") or "")[:8] or None,
        "title": b.get("title"),
        "policy_area": (pa or "")[:100] or None,
        "introduced_date": _parse_date(b.get("introducedDate")),
        "latest_action_date": _parse_date(la.get("actionDate")),
        "latest_action_text": la.get("text"),
        "update_date": _parse_dt(b.get("updateDate") or b.get("updateDateIncludingText")),
        "url": (b.get("legislationUrl") or b.get("url") or "")[:300] or None,
    }


def _primary_sponsor_row(uid: str, b: dict) -> dict | None:
    sponsors = b.get("sponsors") or []
    if not sponsors:
        return None
    s = sponsors[0]
    bio = (s.get("bioguideId") or "").strip()
    if not bio:
        return None
    return {
        "bill_uid": uid,
        "bioguide_id": bio[:7],
        "role": "sponsor",
        "sponsorship_date": _parse_date(s.get("sponsorshipDate")),
        "withdrawn_date": _parse_date(s.get("sponsorshipWithdrawnDate")),
    }


def _cosponsor_row(uid: str, c: dict) -> dict | None:
    bio = (c.get("bioguideId") or "").strip()
    if not bio:
        return None
    return {
        "bill_uid": uid,
        "bioguide_id": bio[:7],
        "role": "cosponsor",
        "sponsorship_date": _parse_date(c.get("sponsorshipDate")),
        "withdrawn_date": _parse_date(c.get("sponsorshipWithdrawnDate")),
    }


def _committee_activity_rows(uid: str, c: dict) -> list[dict]:
    sys_code = _normalize_committee_id(c.get("systemCode"))
    if not sys_code:
        return []
    rows: list[dict] = []
    for act in (c.get("activities") or []):
        d = _parse_date(act.get("date"))
        a_type = (act.get("name") or "")[:60]
        if d is None or not a_type:
            continue
        rows.append({
            "bill_uid": uid,
            "country_code": "US",
            "committee_id": sys_code[:8],
            "activity_date": d,
            "activity_type": a_type,
        })
    return rows


def _action_row(uid: str, a: dict) -> dict | None:
    d = _parse_date(a.get("actionDate"))
    txt = a.get("text")
    if d is None or not txt:
        return None
    chamber = a.get("sourceSystem") or {}
    # Deterministic action_id from natural key — guarantees idempotent re-runs
    # of Pass C without a DB-side UniqueConstraint migration. See
    # NAMESPACE_BILL_ACTIONS comment above.
    aid = uuid.uuid5(NAMESPACE_BILL_ACTIONS, f"{uid}|{d.isoformat()}|{txt[:500]}")
    return {
        "action_id": aid,
        "bill_uid": uid,
        "action_date": d,
        "action_text": txt,
        "action_type": (a.get("type") or "")[:60] or None,
        "action_chamber": (chamber.get("name") or "")[:8] or None,
    }


def _to_hearing_row(h: dict) -> dict | None:
    jacket = h.get("jacketNumber")
    cong = h.get("congress")
    chmb = (h.get("chamber") or "")[:8]
    if jacket is None or cong is None or not chmb:
        return None
    cmts = h.get("committees") or []
    sys_code = None
    if cmts:
        sys_code = _normalize_committee_id(cmts[0].get("systemCode"))
    formats = h.get("formats") or []
    pdf_url = next((f.get("url") for f in formats if f.get("type") == "PDF"), None)
    if not pdf_url and formats:
        pdf_url = formats[0].get("url")
    dates = h.get("dates") or []
    date_held = _parse_date((dates[0] or {}).get("date")) if dates else None
    return {
        "country_code": "US",
        "jacket_number": int(jacket),
        "congress": int(cong),
        "chamber": chmb,
        "title": h.get("title"),
        "date_held": date_held,
        "committee_country_code": "US" if sys_code else None,
        "committee_id": sys_code[:8] if sys_code else None,
        "citation": (h.get("citation") or "")[:200] or None,
        "update_date": _parse_dt(h.get("updateDate")),
        "url": (pdf_url or "")[:300] or None,
    }


# ===========================================================================
#  PASS A — bills list pull
# ===========================================================================

def ingest_bills_list(
    engine: Engine,
    *,
    congresses: list[int] | None = None,
    dry_run: bool = False,
) -> BillsResult:
    """Pass A — paginated list pull, skeleton rows only."""
    res = BillsResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "CONGRESS_API_KEY required"
        return res

    congresses = congresses or DEFAULT_CONGRESSES
    http = PoliticalHTTPClient(source="congress_gov", engine=engine)
    client = CongressGovClient(http)
    state = State(source="congress_gov_bills_list")

    rows_buf: list[dict] = []
    for cong in congresses:
        ckey = f"list/{cong}/done"
        if state.is_done(ckey):
            log.info("[congress] Pass A %d: already complete (per state)", cong)
            continue
        log.info("[congress] Pass A %d: list pull", cong)
        n_for_cong = 0
        for b in client.iter_bills_list(cong):
            row = _to_bill_skeleton_row(b)
            if row is None:
                continue
            rows_buf.append(row)
            n_for_cong += 1
            if len(rows_buf) >= BATCH_SIZE:
                if not dry_run:
                    bulk_insert_ignore(
                        engine, bills, rows_buf,
                        conflict_keys=["bill_uid"],
                    )
                rows_buf = []
        log.info("[congress] Pass A %d: %d bills listed", cong, n_for_cong)
        res.bills_listed += n_for_cong
        state.mark_done(ckey)
        state.flush()
    if rows_buf and not dry_run:
        bulk_insert_ignore(engine, bills, rows_buf, conflict_keys=["bill_uid"])
    res.list_passes = len(congresses)
    return res


# ===========================================================================
#  PASS B — bills detail enrichment
# ===========================================================================

def ingest_bills_detail(
    engine: Engine,
    *,
    congresses: list[int] | None = None,
    bill_limit: int | None = None,
    dry_run: bool = False,
) -> BillsResult:
    """Pass B — for each bill in `bills` (skeleton), fetch detail and update
    policy_area, introduced_date, primary sponsor.
    """
    res = BillsResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "CONGRESS_API_KEY required"
        return res

    congresses = congresses or DEFAULT_CONGRESSES
    http = PoliticalHTTPClient(source="congress_gov", engine=engine)
    client = CongressGovClient(http)
    state = State(source="congress_gov_bills_detail")

    # Pull bills needing detail enrichment (no policy_area yet)
    with engine.connect() as c:
        targets = c.execute(text("""
            SELECT bill_uid, congress, bill_type, bill_number
              FROM alt_political_us.bills
             WHERE policy_area IS NULL
               AND congress = ANY(:cs)
             ORDER BY congress, bill_type, bill_number
        """), {"cs": list(congresses)}).all()
    if bill_limit:
        targets = targets[:bill_limit]
    log.info("[congress] Pass B: %d bills to enrich", len(targets))

    update_buf: list[dict] = []
    sponsor_buf: list[dict] = []

    for i, (uid, cong, btype, num) in enumerate(targets, 1):
        if state.is_done(uid):
            continue
        try:
            b = client.get_bill_detail(int(cong), btype, int(num))
        except Exception as e:
            log.warning("[congress] detail failed for %s: %s", uid, e)
            continue
        row = _enrich_bill_from_detail(b)
        if row:
            update_buf.append(row)
            res.bills_enriched += 1
        sponsor = _primary_sponsor_row(uid, b)
        if sponsor:
            sponsor_buf.append(sponsor)
        state.mark_done(uid)
        if len(update_buf) >= BATCH_SIZE:
            if not dry_run:
                fast_upsert(
                    engine, bills, update_buf,
                    conflict_keys=["bill_uid"],
                    update_cols=["policy_area", "introduced_date",
                                 "latest_action_date", "latest_action_text",
                                 "update_date", "url"],
                )
                if sponsor_buf:
                    fast_upsert(
                        engine, bill_sponsors, sponsor_buf,
                        conflict_keys=["bill_uid", "bioguide_id", "role"],
                        update_cols=["sponsorship_date", "withdrawn_date"],
                    )
                    res.primary_sponsors_inserted += len(sponsor_buf)
            update_buf = []
            sponsor_buf = []
            state.flush()
            log.info("[congress] Pass B progress: %d/%d enriched", i, len(targets))

    if not dry_run:
        if update_buf:
            fast_upsert(
                engine, bills, update_buf,
                conflict_keys=["bill_uid"],
                update_cols=["policy_area", "introduced_date",
                             "latest_action_date", "latest_action_text",
                             "update_date", "url"],
            )
        if sponsor_buf:
            fast_upsert(
                engine, bill_sponsors, sponsor_buf,
                conflict_keys=["bill_uid", "bioguide_id", "role"],
                update_cols=["sponsorship_date", "withdrawn_date"],
            )
            res.primary_sponsors_inserted += len(sponsor_buf)
    state.flush()
    res.detail_passes = 1
    return res


# ===========================================================================
#  PASS C — bills deep fetch (priority-policy only)
# ===========================================================================

def ingest_bills_deep(
    engine: Engine,
    *,
    congresses: list[int] | None = None,
    bill_limit: int | None = None,
    priority_policy_areas: Iterable[str] | None = None,
    dry_run: bool = False,
) -> BillsResult:
    """Pass C — for bills whose `policy_area` ∈ priority set, fetch
    cosponsors + committees + actions sub-resources.
    """
    res = BillsResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "CONGRESS_API_KEY required"
        return res

    congresses = congresses or DEFAULT_CONGRESSES
    priority = frozenset(priority_policy_areas) if priority_policy_areas else PRIORITY_POLICY_AREAS

    http = PoliticalHTTPClient(source="congress_gov", engine=engine)
    client = CongressGovClient(http)
    state = State(source="congress_gov_bills_deep")

    # Pre-load valid committee IDs for FK filtering on bill_committees writes.
    # Congress.gov references committees we may not have (select committees,
    # new committees not in our YAML snapshot). Skipping those rows keeps the
    # FK happy without dropping integrity guarantees.
    with engine.connect() as c:
        valid_committee_ids = {
            r[0] for r in c.execute(text(
                "SELECT committee_id FROM alt_political_us.committees WHERE country_code='US'"
            ))
        }
    log.info("[congress] Pass C: loaded %d valid committee IDs for FK filter",
             len(valid_committee_ids))

    with engine.connect() as c:
        targets = c.execute(text("""
            SELECT bill_uid, congress, bill_type, bill_number, policy_area
              FROM alt_political_us.bills
             WHERE policy_area IS NOT NULL
               AND congress = ANY(:cs)
             ORDER BY congress, bill_type, bill_number
        """), {"cs": list(congresses)}).all()
    if bill_limit:
        targets = targets[:bill_limit]
    log.info("[congress] Pass C: %d bills with policy_area; filtering to %d priority areas",
             len(targets), len(priority))

    cos_buf: list[dict] = []
    cmt_buf: list[dict] = []
    act_buf: list[dict] = []

    for i, (uid, cong, btype, num, pa) in enumerate(targets, 1):
        if state.is_done(uid):
            continue
        if pa not in priority:
            res.bills_skipped_non_priority += 1
            state.mark_done(uid)
            continue
        try:
            for c_row in client.iter_bill_subresource(int(cong), btype, int(num),
                                                     "cosponsors", "cosponsors"):
                row = _cosponsor_row(uid, c_row)
                if row:
                    cos_buf.append(row)
            for cm in client.iter_bill_subresource(int(cong), btype, int(num),
                                                   "committees", "committees"):
                rows = _committee_activity_rows(uid, cm)
                # FK-filter: skip rows where committee_id isn't in our local table
                cmt_buf.extend(r for r in rows if r["committee_id"] in valid_committee_ids)
            for a in client.iter_bill_subresource(int(cong), btype, int(num),
                                                  "actions", "actions"):
                row = _action_row(uid, a)
                if row:
                    act_buf.append(row)
        except Exception as e:
            log.warning("[congress] deep-fetch failed for %s: %s", uid, e)
            continue
        state.mark_done(uid)

        if len(cos_buf) + len(cmt_buf) + len(act_buf) >= BATCH_SIZE:
            if not dry_run:
                if cos_buf:
                    fast_upsert(
                        engine, bill_sponsors, cos_buf,
                        conflict_keys=["bill_uid", "bioguide_id", "role"],
                        update_cols=["sponsorship_date", "withdrawn_date"],
                    )
                    res.cosponsors_inserted += len(cos_buf)
                if cmt_buf:
                    bulk_insert_ignore(
                        engine, bill_committees, cmt_buf,
                        conflict_keys=["bill_uid", "country_code", "committee_id",
                                       "activity_date", "activity_type"],
                    )
                    res.committees_inserted += len(cmt_buf)
                if act_buf:
                    bulk_insert_ignore(
                        engine, bill_actions, act_buf,
                        conflict_keys=["action_id"],
                    )
                    res.actions_inserted += len(act_buf)
            cos_buf, cmt_buf, act_buf = [], [], []
            state.flush()
            if i % 200 == 0:
                log.info("[congress] Pass C progress: %d/%d  cos+cmt+act=%d/%d/%d",
                         i, len(targets),
                         res.cosponsors_inserted, res.committees_inserted, res.actions_inserted)

    if not dry_run:
        if cos_buf:
            fast_upsert(
                engine, bill_sponsors, cos_buf,
                conflict_keys=["bill_uid", "bioguide_id", "role"],
                update_cols=["sponsorship_date", "withdrawn_date"],
            )
            res.cosponsors_inserted += len(cos_buf)
        if cmt_buf:
            bulk_insert_ignore(
                engine, bill_committees, cmt_buf,
                conflict_keys=["bill_uid", "country_code", "committee_id",
                               "activity_date", "activity_type"],
            )
            res.committees_inserted += len(cmt_buf)
        if act_buf:
            bulk_insert_ignore(
                engine, bill_actions, act_buf,
                conflict_keys=["action_id"],
            )
            res.actions_inserted += len(act_buf)
    state.flush()
    res.deep_fetch_passes = 1
    return res


# ===========================================================================
#  HEARINGS — list + detail
# ===========================================================================

def ingest_hearings(
    engine: Engine,
    *,
    congresses: list[int] | None = None,
    hearing_limit: int | None = None,
    dry_run: bool = False,
) -> HearingsResult:
    """Pull all hearings from `congresses`, list+detail.

    `hearing_witnesses` stays empty in v1 — Congress.gov doesn't expose
    structured witness lists; transcripts at `formats[].url` would need a
    separate parser.
    """
    res = HearingsResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "CONGRESS_API_KEY required"
        return res

    congresses = congresses or DEFAULT_CONGRESSES
    http = PoliticalHTTPClient(source="congress_gov", engine=engine)
    client = CongressGovClient(http)
    state = State(source="congress_gov_hearings")

    # Pre-load valid committee IDs to NULL out hearing.committee_id when missing
    with engine.connect() as c:
        valid_committee_ids = {
            r[0] for r in c.execute(text(
                "SELECT committee_id FROM alt_political_us.committees WHERE country_code='US'"
            ))
        }

    rows_buf: list[dict] = []
    seen_count = 0

    for cong in congresses:
        log.info("[congress] hearings %d: list+detail", cong)
        for h_summary in client.iter_hearings_list(cong):
            seen_count += 1
            res.hearings_listed += 1
            jacket = h_summary.get("jacketNumber")
            chmb = (h_summary.get("chamber") or "").lower()
            if jacket is None or not chmb:
                continue
            ckey = f"{cong}/{chmb}/{jacket}"
            if state.is_done(ckey):
                continue
            if hearing_limit and seen_count > hearing_limit:
                break
            try:
                det = client.get_hearing_detail(cong, chmb, int(jacket))
            except Exception as e:
                log.warning("[congress] hearing detail %s failed: %s", ckey, e)
                continue
            row = _to_hearing_row(det)
            if row:
                # FK soft-null: drop committee_id if not in our local table
                if row["committee_id"] and row["committee_id"] not in valid_committee_ids:
                    row["committee_id"] = None
                    row["committee_country_code"] = None
                rows_buf.append(row)
                res.hearings_detailed += 1
            state.mark_done(ckey)
            if len(rows_buf) >= BATCH_SIZE:
                if not dry_run:
                    bulk_insert_ignore(
                        engine, hearings, rows_buf,
                        conflict_keys=["country_code", "jacket_number"],
                    )
                    res.hearings_inserted += len(rows_buf)
                rows_buf = []
                state.flush()
        log.info("[congress] hearings %d: listed=%d  inserted=%d",
                 cong, res.hearings_listed, res.hearings_inserted)

    if rows_buf and not dry_run:
        bulk_insert_ignore(
            engine, hearings, rows_buf,
            conflict_keys=["country_code", "jacket_number"],
        )
        res.hearings_inserted += len(rows_buf)
    state.flush()
    return res


# ===========================================================================
#  TOP-LEVEL
# ===========================================================================

def ingest_congress_gov(
    engine: Engine,
    *,
    congresses: list[int] | None = None,
    do_list: bool = True,
    do_detail: bool = True,
    do_deep: bool = True,
    do_hearings: bool = True,
    dry_run: bool = False,
    **_kw,
) -> IngestResult:
    """Top-level Congress.gov ingestion. Default: all four passes."""
    res = IngestResult()
    if _key_missing():
        res.skipped_no_key = 1
        res.note = "CONGRESS_API_KEY required; sign up at https://api.congress.gov/sign-up"
        log.info("[congress] %s", res.note)
        return res

    bills_res = BillsResult()
    if do_list:
        r = ingest_bills_list(engine, congresses=congresses, dry_run=dry_run)
        bills_res.bills_listed += r.bills_listed
        bills_res.list_passes = r.list_passes
    if do_detail:
        r = ingest_bills_detail(engine, congresses=congresses, dry_run=dry_run)
        bills_res.bills_enriched += r.bills_enriched
        bills_res.primary_sponsors_inserted += r.primary_sponsors_inserted
        bills_res.detail_passes = r.detail_passes
    if do_deep:
        r = ingest_bills_deep(engine, congresses=congresses, dry_run=dry_run)
        bills_res.cosponsors_inserted += r.cosponsors_inserted
        bills_res.committees_inserted += r.committees_inserted
        bills_res.actions_inserted += r.actions_inserted
        bills_res.bills_skipped_non_priority += r.bills_skipped_non_priority
        bills_res.deep_fetch_passes = r.deep_fetch_passes
    res.bills = bills_res

    if do_hearings:
        res.hearings_res = ingest_hearings(engine, congresses=congresses, dry_run=dry_run)

    return res


# ===========================================================================
#  CLI
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--congress", type=int, action="append", default=None,
                    help="Congress number (repeat for multiple). Default: 117 118 119")
    ap.add_argument("--list", action="store_true", help="Run Pass A (list)")
    ap.add_argument("--detail", action="store_true", help="Run Pass B (detail enrichment)")
    ap.add_argument("--deep", action="store_true", help="Run Pass C (deep-fetch priority bills)")
    ap.add_argument("--hearings", action="store_true", help="Run hearings list+detail")
    ap.add_argument("--bill-limit", type=int, default=None, help="Cap bills processed (testing)")
    ap.add_argument("--hearing-limit", type=int, default=None,
                    help="Cap hearings processed (testing)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    flags = (args.list, args.detail, args.deep, args.hearings)
    if not any(flags):
        # Default = run everything
        do_list = do_detail = do_deep = do_hearings = True
    else:
        do_list, do_detail, do_deep, do_hearings = flags

    from factorlab.storage.db import get_engine
    res = ingest_congress_gov(
        get_engine(),
        congresses=args.congress,
        do_list=do_list, do_detail=do_detail, do_deep=do_deep, do_hearings=do_hearings,
        dry_run=args.dry_run,
    )
    print(f"\nIngestResult: {res}")
