"""Refresh `bioguide_id` for all rows in alt_political_us.legislator_trades.

Re-applies the current StrictBioguideMatcher to existing rows without re-fetching
or re-parsing source data. Useful after matcher edits.

Run:
    python scripts/refresh_bioguide.py
    python scripts/refresh_bioguide.py --source senate_stock_watcher_historical  # one source
    python scripts/refresh_bioguide.py --dry-run                                  # no writes
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

# Path layout: scripts/us/political/_maintenance/<file>.py
#   parents[0]=_maintenance, [1]=political, [2]=us, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import text  # noqa: E402

from factorlab.countries.us.political._bioguide import StrictBioguideMatcher  # noqa: E402
from factorlab.storage.db import get_engine  # noqa: E402

log = logging.getLogger(__name__)

# source → chamber lookup (matches what each ingest module uses)
SOURCE_CHAMBER = {
    "senate_stock_watcher_historical": "sen",
    "senate_efd_ptr": "sen",
    "house_clerk_ptr": "rep",
}


def main(source_filter: str | None, dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    engine = get_engine()
    matcher = StrictBioguideMatcher(engine)

    where = ""
    params: dict = {}
    if source_filter:
        where = "WHERE source = :src"
        params = {"src": source_filter}

    with engine.connect() as c:
        rows = list(c.execute(text(f"""
            SELECT trade_id, source, chamber, legislator_name_raw, transaction_date, bioguide_id
            FROM alt_political_us.legislator_trades
            {where}
        """), params))

    log.info("[refresh] %d rows to inspect", len(rows))

    updates: list[tuple] = []
    stats: Counter = Counter()
    for r in rows:
        chamber = SOURCE_CHAMBER.get(r.source)
        if not chamber:
            stats["unknown_source"] += 1
            continue
        new_bio = matcher.resolve(
            full_name=r.legislator_name_raw,
            chamber=chamber,
            trade_date=r.transaction_date,
        )
        if new_bio != r.bioguide_id:
            updates.append((r.trade_id, new_bio))
            if r.bioguide_id and not new_bio:
                stats["resolved->null"] += 1
            elif not r.bioguide_id and new_bio:
                stats["null->resolved"] += 1
            elif r.bioguide_id and new_bio and r.bioguide_id != new_bio:
                stats["bioguide_changed"] += 1
        else:
            stats["unchanged"] += 1

    log.info("[refresh] stats: %s", dict(stats))
    log.info("[refresh] %d updates pending", len(updates))

    if dry_run or not updates:
        return

    with engine.begin() as c:
        c.execute(
            text("UPDATE alt_political_us.legislator_trades SET bioguide_id = :bio WHERE trade_id = :tid"),
            [{"bio": b, "tid": tid} for tid, b in updates],
        )
    log.info("[refresh] applied %d updates", len(updates))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="Restrict to one source")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(args.source, args.dry_run)
