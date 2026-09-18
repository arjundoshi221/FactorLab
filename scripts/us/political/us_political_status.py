"""Quick status snapshot for the political pipeline — designed for mid-backfill checks.

Lighter than `verify_political.py` (the weekly integrity battery). Reports:
  1. Row counts for the FEC + Congress.gov tables we just built
  2. Per-endpoint last-fetch timestamp + recent fetch count from audit.raw_archive
  3. State-checkpoint progress (PAC-cycles done, bills detailed/deep-fetched, etc.)
  4. Disk cache footprint under data/political/raw/

Usage:
    "C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/political/us_political_status.py

No DB writes. Read-only. Safe to run while a backfill is in progress.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(message)s")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Path layout: scripts/us/political/<file>.py (multi-vendor orchestrator)
#   parents[0]=political, [1]=us, [2]=scripts, [3]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import text     # noqa: E402

from factorlab.storage.db import get_engine     # noqa: E402

# Tables to count; ordered by domain
TABLES = [
    # legislator dim
    "legislators", "legislator_terms", "legislator_fec_ids",
    "committees", "committee_assignments",
    # event tables
    "legislator_trades", "gov_contracts",
    "lobbying_filings", "lobbying_activities",
    # FEC
    "fec_committees", "campaign_donations",
    # Congress.gov
    "bills", "bill_sponsors", "bill_committees", "bill_actions", "hearings",
]

ENDPOINTS = [
    "fec", "congress_gov", "lda", "usaspending", "finnhub_usa_spending",
    "house_clerk_ptr", "senate_efd_ptr", "senate_stock_watcher_historical",
    "legislators_yaml",
]

STATE_DIR = PROJECT_ROOT / "data" / "political" / "_state"
RAW_DIR = PROJECT_ROOT / "data" / "political" / "raw"


def _print_table(rows: list[tuple]) -> None:
    if not rows:
        print("  (empty)")
        return
    n_cols = len(rows[0])
    widths = [max(len(str(r[i])) for r in rows) for i in range(n_cols)]
    for r in rows:
        print("  " + "  ".join(f"{str(v):<{widths[i]}}" for i, v in enumerate(r)))


def section_row_counts(eng) -> None:
    print("\n=== 1. Row counts (alt_political_us) ===")
    rows: list[tuple] = []
    with eng.connect() as c:
        for t in TABLES:
            try:
                n = c.execute(text(f"SELECT count(*) FROM alt_political_us.{t}")).scalar()
                rows.append((t, f"{n:,}"))
            except Exception as e:
                rows.append((t, f"ERROR: {str(e)[:40]}"))
    _print_table(rows)


def section_endpoints(eng) -> None:
    print("\n=== 2. Per-endpoint audit activity (all endpoints with traffic) ===")
    rows: list[tuple] = []
    with eng.connect() as c:
        # Group by endpoint code; only show endpoints with at least one row.
        # Sources may register under either vendor-level codes ('house_clerk',
        # 'fec') or per-endpoint codes ('house_clerk_ptr', 'senate_efd_ptr').
        result = c.execute(text("""
            SELECT e.code,
                   count(*) AS total,
                   count(*) FILTER (WHERE a.fetched_at > now() - INTERVAL '24 hours') AS last_24h,
                   max(a.fetched_at) AS last_fetch
              FROM audit.raw_archive a
              JOIN ref.data_endpoints e ON e.id = a.endpoint_id
             GROUP BY e.code
             ORDER BY last_fetch DESC NULLS LAST, e.code
        """)).fetchall()
        for r in result:
            code, total, last_24h, last_fetch = r
            last_str = last_fetch.strftime("%Y-%m-%d %H:%M:%S") if last_fetch else "—"
            rows.append((code, f"total={total:,}", f"24h={last_24h:,}", f"last={last_str}"))
    _print_table(rows)


def section_state_files() -> None:
    print("\n=== 3. State checkpoints ===")
    if not STATE_DIR.exists():
        print("  (no state dir)")
        return
    rows: list[tuple] = []
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            n = len(data) if isinstance(data, list) else "obj"
        except Exception as e:
            n = f"ERROR: {str(e)[:30]}"
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        rows.append((f.name, f"entries={n}", f"updated={mtime.strftime('%Y-%m-%d %H:%M')}"))
    _print_table(rows)


def section_cache_footprint() -> None:
    print("\n=== 4. Raw cache footprint (data/political/raw/) ===")
    if not RAW_DIR.exists():
        print("  (no raw dir)")
        return
    rows: list[tuple] = []
    for sub in sorted(RAW_DIR.iterdir()):
        if not sub.is_dir():
            continue
        files = [f for f in sub.rglob("*") if f.is_file()]
        size = sum(f.stat().st_size for f in files)
        rows.append((sub.name, f"{size / 1e6:.1f} MB", f"{len(files):,} files"))
    _print_table(rows)


def section_fec_progress(eng) -> None:
    print("\n=== 5. FEC backfill progress ===")
    with eng.connect() as c:
        # Mode A: total ticker-resolved corp PACs
        n_pacs = c.execute(text("""
            SELECT count(*) FROM alt_political_us.fec_committees
             WHERE country_code='US' AND organization_type='C'
               AND sponsor_company_ticker IS NOT NULL
        """)).scalar()
        # PCCs (Mode B target)
        n_pccs = c.execute(text("""
            SELECT count(*) FROM alt_political_us.fec_committees
             WHERE country_code='US' AND designation='P'
        """)).scalar()
        # Donations split
        n_a = c.execute(text("""
            SELECT count(*) FROM alt_political_us.campaign_donations
             WHERE donor_committee_id IS NOT NULL
        """)).scalar()
        n_b = c.execute(text("""
            SELECT count(*) FROM alt_political_us.campaign_donations
             WHERE donor_committee_id IS NULL
        """)).scalar()
    state_a = STATE_DIR / "fec_donations_mode_a.json"
    state_b = STATE_DIR / "fec_donations_mode_b.json"

    def _state_count(p: Path) -> int:
        if not p.exists():
            return 0
        try:
            return len(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return 0

    rows = [
        ("Mode A target (corp PACs × cycles)", f"{n_pacs * 7:,}",
         f"done={_state_count(state_a):,}", f"rows={n_a:,}"),
        ("Mode B target (PCCs × cycles)", f"{n_pccs * 5:,}",
         f"done={_state_count(state_b):,}", f"rows={n_b:,}"),
    ]
    _print_table(rows)


def section_congress_progress(eng) -> None:
    print("\n=== 6. Congress.gov backfill progress ===")
    with eng.connect() as c:
        rows = c.execute(text("""
            SELECT bill_type, count(*) AS skel,
                   count(policy_area) AS enriched
              FROM alt_political_us.bills
             GROUP BY bill_type ORDER BY bill_type
        """)).all()
    table = [("type", "skeleton", "enriched", "%")]
    for r in rows:
        bt, sk, en = r
        pct = f"{(en / sk * 100):.0f}%" if sk else "—"
        table.append((bt, f"{sk:,}", f"{en:,}", pct))
    _print_table(table)
    state_deep = _state_count_safe(STATE_DIR / "congress_gov_bills_deep.json")
    state_detail = _state_count_safe(STATE_DIR / "congress_gov_bills_detail.json")
    state_hearings = _state_count_safe(STATE_DIR / "congress_gov_hearings.json")
    print(f"  state checkpoints  detail={state_detail:,}  deep={state_deep:,}  hearings={state_hearings:,}")


def _state_count_safe(p: Path) -> int:
    if not p.exists():
        return 0
    try:
        return len(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return 0


def main() -> int:
    eng = get_engine()
    print(f"Political pipeline status — {datetime.now(timezone.utc).isoformat()}")
    section_row_counts(eng)
    section_endpoints(eng)
    section_state_files()
    section_cache_footprint()
    section_fec_progress(eng)
    section_congress_progress(eng)
    return 0


if __name__ == "__main__":
    sys.exit(main())
