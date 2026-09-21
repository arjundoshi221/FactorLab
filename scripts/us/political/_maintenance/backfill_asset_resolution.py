"""Dry-run / apply asset_type_code + ticker backfill on legislator_trades.

Walks every row WHERE ticker IS NULL OR asset_type_code IS NULL, runs the
AssetClassifier, and either reports proposed updates (--dry-run, default) or
applies them (--apply).

Run:
    python scripts/backfill_asset_resolution.py                # dry-run
    python scripts/backfill_asset_resolution.py --apply        # update DB
    python scripts/backfill_asset_resolution.py --sample 20    # show 20 example resolutions
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from sqlalchemy import text

from factorlab.countries.us.political._asset_classifier import AssetClassifier
from factorlab.storage.db import get_engine

log = logging.getLogger(__name__)


def main(*, apply: bool, sample: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(name)s  %(message)s")
    engine = get_engine()
    classifier = AssetClassifier()

    with engine.connect() as c:
        rows = list(c.execute(text("""
            SELECT trade_id, asset_name_raw, ticker, asset_type_code, source
            FROM alt_political_us.legislator_trades
            WHERE ticker IS NULL OR asset_type_code IS NULL
        """)))
    log.info("[backfill] %d rows to inspect", len(rows))

    # Stats
    by_reason: Counter = Counter()
    ticker_set: Counter = Counter()
    code_set: Counter = Counter()
    examples_by_reason: dict[str, list] = {}
    updates: list[dict] = []
    new_tickers = 0  # rows where ticker WAS NULL and is now resolved
    new_codes = 0    # rows where code WAS NULL and is now resolved

    for r in rows:
        result = classifier.classify(r.asset_name_raw)
        by_reason[result.reason] += 1
        if result.ticker:
            ticker_set[result.ticker] += 1
        if result.asset_type_code:
            code_set[result.asset_type_code] += 1

        # Collect 3 examples per reason for display
        examples_by_reason.setdefault(result.reason, [])
        if len(examples_by_reason[result.reason]) < 3:
            examples_by_reason[result.reason].append(
                (r.asset_name_raw[:80], result.ticker, result.asset_type_code)
            )

        # Build update payload — only set fields that are currently NULL
        new_ticker = result.ticker if r.ticker is None else None
        new_code = result.asset_type_code if r.asset_type_code is None else None
        if new_ticker:
            new_tickers += 1
        if new_code:
            new_codes += 1
        if new_ticker is not None or new_code is not None:
            updates.append({
                "tid": r.trade_id,
                "ticker": new_ticker if new_ticker is not None else r.ticker,
                "code": new_code if new_code is not None else r.asset_type_code,
            })

    # ---- report ------------------------------------------------------------
    print()
    print("=" * 78)
    print(f"  Dry-run results: {len(rows):,} rows inspected")
    print("=" * 78)
    # Pre-existing state
    pre_with_ticker = sum(1 for r in rows if r.ticker)
    pre_with_code = sum(1 for r in rows if r.asset_type_code)
    print(f"  pre-existing ticker:      {pre_with_ticker:,}")
    print(f"  pre-existing asset_code:  {pre_with_code:,}")
    print(f"  NEW tickers added:        {new_tickers:,}")
    print(f"  NEW asset_codes added:    {new_codes:,}")
    print(f"  total rows updated:       {len(updates):,}")
    final_ticker = pre_with_ticker + new_tickers
    final_code = pre_with_code + new_codes
    print(f"  final ticker count:       {final_ticker:,} / {len(rows):,} inspected")
    print(f"  final asset_code count:   {final_code:,} / {len(rows):,} inspected")

    print()
    print("=== By classification reason ===")
    for reason, n in by_reason.most_common():
        print(f"  {reason:30s}  n={n:>6,}")
        for raw, t, code in examples_by_reason.get(reason, [])[:2]:
            print(f"     {raw:78s}  ticker={t}  code={code}")

    print()
    print(f"=== Top 15 newly-attached tickers ===")
    for t, n in ticker_set.most_common(15):
        print(f"  {t:8s}  n={n:>5,}")

    print()
    print(f"=== Asset_type_code distribution proposed ===")
    for code, n in code_set.most_common():
        print(f"  {code:5s}  n={n:>6,}")

    if sample:
        print()
        print(f"=== {sample} random sample resolutions ===")
        import random
        for r in random.sample(rows, min(sample, len(rows))):
            res = classifier.classify(r.asset_name_raw)
            print(f"  [{res.reason:25s}]  '{r.asset_name_raw[:60]}'  -> "
                  f"ticker={res.ticker}  code={res.asset_type_code}  conf={res.confidence:.2f}")

    if not apply:
        print()
        print("(dry-run mode — no DB writes. Re-run with --apply to update.)")
        return

    if not updates:
        print()
        print("(no updates needed)")
        return

    print()
    print(f"Applying {len(updates):,} updates...")
    BATCH = 1000
    with engine.begin() as c:
        for i in range(0, len(updates), BATCH):
            chunk = updates[i:i + BATCH]
            c.execute(
                text("""
                    UPDATE alt_political_us.legislator_trades
                    SET ticker = :ticker, asset_type_code = :code
                    WHERE trade_id = :tid
                """),
                chunk,
            )
    log.info("[backfill] applied %d updates", len(updates))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write updates to DB (default: dry-run)")
    ap.add_argument("--sample", type=int, default=0, help="Print N random sample resolutions")
    args = ap.parse_args()
    main(apply=args.apply, sample=args.sample)
