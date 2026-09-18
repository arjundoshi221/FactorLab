"""Legislators YAML → 5 alt_political_us dim tables.

Run:
    python -m factorlab.countries.us.political.legislators.ingest
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.engine import Engine

from factorlab.countries.us.political._client import PoliticalHTTPClient
from factorlab.countries.us.political._db import fast_upsert
from factorlab.countries.us.political.legislators.yaml_fetcher import fetch_all
from factorlab.storage.schemas.alt_political_us import (
    committee_assignments,
    committees,
    legislator_fec_ids,
    legislator_terms,
    legislators,
)

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    legislators_n: int = 0
    legislator_terms_n: int = 0
    legislator_fec_ids_n: int = 0
    committees_n: int = 0
    committee_assignments_n: int = 0
    files_fetched: list[str] = field(default_factory=list)


# ---- helpers --------------------------------------------------------------

def _dedup(rows: list[dict], *, key) -> list[dict]:
    """Last-row-wins dedup by composite key fn."""
    seen: dict = {}
    for r in rows:
        seen[key(r)] = r
    return list(seen.values())


# ---- transforms ------------------------------------------------------------

def _legislator_row(rec: dict, *, in_office: bool) -> dict:
    ids = rec.get("id") or {}
    name = rec.get("name") or {}
    bio = rec.get("bio") or {}
    bday = bio.get("birthday")
    return {
        "bioguide_id": ids.get("bioguide"),
        "country_code": "US",
        "first_name": name.get("first") or "",
        "middle_name": name.get("middle"),
        "last_name": name.get("last") or "",
        "suffix": name.get("suffix"),
        "nickname": name.get("nickname"),
        "official_full": name.get("official_full"),
        "gender": (bio.get("gender") or "")[:1] or None,
        "birthday": _parse_date(bday) if bday else None,
        "thomas_id": str(ids["thomas"]) if ids.get("thomas") else None,
        "govtrack_id": ids.get("govtrack"),
        "opensecrets_cid": ids.get("opensecrets"),
        "wikipedia_id": (ids.get("wikipedia") or "")[:200] or None,
        "ballotpedia_id": (ids.get("ballotpedia") or "")[:200] or None,
        "fec_candidate_id_primary": (ids.get("fec") or [None])[0] if ids.get("fec") else None,
        "in_office": in_office,
    }


def _parse_date(s) -> date | None:
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None


def _term_rows(rec: dict) -> list[dict]:
    bioguide = (rec.get("id") or {}).get("bioguide")
    if not bioguide:
        return []
    out = []
    for t in (rec.get("terms") or []):
        ts = _parse_date(t.get("start"))
        te = _parse_date(t.get("end"))
        if not ts or not te:
            continue
        out.append({
            "bioguide_id": bioguide,
            "term_start": ts,
            "term_end": te,
            "chamber": t.get("type"),  # 'sen' or 'rep'
            "state": (t.get("state") or "")[:2],
            "district": t.get("district"),
            "party": t.get("party"),
            "congress_number": _term_to_congress(ts),
        })
    return out


def _term_to_congress(start: date) -> int | None:
    """Approximate Congress # from a term-start date.

    Congress N starts Jan 3 of year (1789 + 2*(N-1)). For modern terms,
    floor((year - 1789)/2) + 1, with a +1 nudge if started after Jan 3.
    """
    if not start:
        return None
    n = (start.year - 1789) // 2 + 1
    return n


def _fec_id_rows(rec: dict) -> list[dict]:
    bioguide = (rec.get("id") or {}).get("bioguide")
    fec_ids = (rec.get("id") or {}).get("fec") or []
    if not bioguide or not fec_ids:
        return []
    # Determine office from latest term
    latest = (rec.get("terms") or [{}])[-1]
    office = {"sen": "S", "rep": "H"}.get(latest.get("type"), "H")
    state = (latest.get("state") or "")[:2] or None
    district = latest.get("district")
    out = []
    for fid in fec_ids:
        if not fid:
            continue
        out.append({
            "fec_candidate_id": fid,
            "country_code": "US",
            "bioguide_id": bioguide,
            "office": office if fid[0] == office else fid[0],
            "state": state,
            "district": district,
            "first_election_year": None,
        })
    return out


def _committee_row(c: dict, is_current: bool) -> dict:
    return {
        "country_code": "US",
        "committee_id": c.get("thomas_id"),
        "chamber": c.get("type"),
        "name": (c.get("name") or "")[:300],
        "parent_country_code": None,
        "parent_committee_id": None,
        "jurisdiction": c.get("jurisdiction"),
        "url": (c.get("url") or "")[:300] or None,
        "rss_url": (c.get("rss_url") or "")[:300] or None,
        "is_current": is_current,
    }


def _subcommittee_rows(parent: dict, is_current: bool) -> list[dict]:
    """Flatten subcommittees: each gets thomas_id like 'SSAS01' (parent + 2-char subcode)."""
    rows = []
    parent_id = parent.get("thomas_id")
    chamber = parent.get("type")
    for sub in (parent.get("subcommittees") or []):
        sub_id = f"{parent_id}{sub.get('thomas_id', '')}"
        rows.append({
            "country_code": "US",
            "committee_id": sub_id,
            "chamber": chamber,
            "name": (sub.get("name") or "")[:300],
            "parent_country_code": "US",
            "parent_committee_id": parent_id,
            "jurisdiction": None,
            "url": None,
            "rss_url": None,
            "is_current": is_current,
        })
    return rows


def _membership_rows(membership: dict) -> list[dict]:
    """`membership` is a dict { committee_thomas_id: [member dicts] }."""
    out = []
    today = date.today()
    # Heuristic: Congress 119 started Jan 2025
    congress_no = 119 if today >= date(2025, 1, 3) else 118
    for cid, members in (membership or {}).items():
        for m in (members or []):
            bio = m.get("bioguide")
            if not bio:
                continue
            title = m.get("title") or "member"
            # Map LDA-friendly titles
            role = title.lower().replace(" ", "_") if title else "member"
            role = {
                "chairman": "chair", "chair": "chair",
                "chairwoman": "chairwoman", "cochairman": "cochairman",
                "vice_chairman": "vice_chairman", "vice_chair": "vice_chair",
                "vice_chairwoman": "vice_chairwoman",
                "ranking_member": "ranking_member",
                "ex_officio": "ex_officio",
                "member": "member",
            }.get(role, "member")
            out.append({
                "country_code": "US",
                "committee_id": cid,
                "bioguide_id": bio,
                "congress_number": congress_no,
                "role": role,
                "rank": m.get("rank"),
                "valid_from": date(2025, 1, 3) if congress_no == 119 else date(2023, 1, 3),
                "valid_to": None,
            })
    return out


# ---- main entrypoint -------------------------------------------------------

def ingest_legislators(
    engine: Engine,
    *,
    full_backfill: bool = True,
    dry_run: bool = False,
) -> IngestResult:
    """Pull all 6 YAMLs and load 5 dim tables. Idempotent."""
    log.info("[legislators] starting (full_backfill=%s, dry_run=%s)", full_backfill, dry_run)

    client = PoliticalHTTPClient(source="legislators", engine=engine)
    yamls = fetch_all(client)
    result = IngestResult(files_fetched=list(yamls.keys()))

    # ---- legislators (current + historical) ----
    leg_rows: list[dict] = []
    term_rows: list[dict] = []
    fec_rows: list[dict] = []

    for rec in (yamls.get("legislators-current.yaml") or []):
        leg_rows.append(_legislator_row(rec, in_office=True))
        term_rows.extend(_term_rows(rec))
        fec_rows.extend(_fec_id_rows(rec))

    for rec in (yamls.get("legislators-historical.yaml") or []):
        row = _legislator_row(rec, in_office=False)
        if row.get("bioguide_id"):
            leg_rows.append(row)
            term_rows.extend(_term_rows(rec))
            fec_rows.extend(_fec_id_rows(rec))

    # Drop rows w/o PK + dedup. Current-YAML entries override historical
    # (current is iterated first; later duplicates in historical are dropped).
    leg_rows = _dedup(
        [r for r in leg_rows if r.get("bioguide_id")],
        key=lambda r: r["bioguide_id"],
    )
    term_rows = _dedup(
        term_rows,
        key=lambda r: (r["bioguide_id"], r["term_start"]),
    )
    fec_rows = _dedup(
        [r for r in fec_rows if r.get("fec_candidate_id")],
        key=lambda r: r["fec_candidate_id"],
    )
    log.info("[legislators] prepared rows: legislators=%d terms=%d fec_ids=%d",
             len(leg_rows), len(term_rows), len(fec_rows))

    # ---- committees ----
    com_rows: list[dict] = []
    for c in (yamls.get("committees-current.yaml") or []):
        com_rows.append(_committee_row(c, is_current=True))
        com_rows.extend(_subcommittee_rows(c, is_current=True))
    for c in (yamls.get("committees-historical.yaml") or []):
        com_rows.append(_committee_row(c, is_current=False))
        com_rows.extend(_subcommittee_rows(c, is_current=False))
    com_rows = _dedup(
        [r for r in com_rows if r.get("committee_id")],
        key=lambda r: (r["country_code"], r["committee_id"]),
    )
    log.info("[legislators] prepared rows: committees=%d", len(com_rows))

    # ---- committee_assignments ----
    asg_rows = _dedup(
        _membership_rows(yamls.get("committee-membership-current.yaml") or {}),
        key=lambda r: (r["country_code"], r["committee_id"], r["bioguide_id"], r["congress_number"]),
    )
    log.info("[legislators] prepared rows: committee_assignments=%d", len(asg_rows))

    if dry_run:
        return IngestResult(
            legislators_n=len(leg_rows),
            legislator_terms_n=len(term_rows),
            legislator_fec_ids_n=len(fec_rows),
            committees_n=len(com_rows),
            committee_assignments_n=len(asg_rows),
            files_fetched=list(yamls.keys()),
        )

    # ---- write order: legislators → committees → terms/fec_ids/assignments
    fast_upsert(
        engine, legislators, leg_rows,
        conflict_keys=["bioguide_id"],
        update_cols=["country_code", "first_name", "middle_name", "last_name",
                     "suffix", "nickname", "official_full", "gender", "birthday",
                     "thomas_id", "govtrack_id", "opensecrets_cid",
                     "wikipedia_id", "ballotpedia_id",
                     "fec_candidate_id_primary", "in_office"],
    )
    result.legislators_n = len(leg_rows)

    fast_upsert(
        engine, committees, com_rows,
        conflict_keys=["country_code", "committee_id"],
        update_cols=["chamber", "name", "parent_country_code", "parent_committee_id",
                     "jurisdiction", "url", "rss_url", "is_current"],
    )
    result.committees_n = len(com_rows)

    if term_rows:
        fast_upsert(
            engine, legislator_terms, term_rows,
            conflict_keys=["bioguide_id", "term_start"],
            update_cols=["term_end", "chamber", "state", "district",
                         "party", "congress_number"],
        )
        result.legislator_terms_n = len(term_rows)

    if fec_rows:
        fast_upsert(
            engine, legislator_fec_ids, fec_rows,
            conflict_keys=["fec_candidate_id"],
            update_cols=["country_code", "bioguide_id", "office", "state",
                         "district", "first_election_year"],
        )
        result.legislator_fec_ids_n = len(fec_rows)

    if asg_rows:
        fast_upsert(
            engine, committee_assignments, asg_rows,
            conflict_keys=["country_code", "committee_id", "bioguide_id", "congress_number"],
            update_cols=["role", "rank", "valid_from", "valid_to"],
        )
        result.committee_assignments_n = len(asg_rows)

    log.info("[legislators] done: %s", result)
    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    from factorlab.storage.db import engine
    res = ingest_legislators(engine, dry_run="--dry-run" in sys.argv)
    print(f"\nIngestResult: {res}")
