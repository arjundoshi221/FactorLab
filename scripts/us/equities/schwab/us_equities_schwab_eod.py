#!/usr/bin/env python3
"""us_equities_schwab_eod -- catch-up daily R3K backfill on EOD cron.

Run model: short-lived cron job. Pulls the last N days of daily bars (default 7d
overlap for resilience) for the R3K universe and upserts into
``market_us.fact_equity``. The unique index (instrument_id, freq_id, bar_time)
makes overlap with prior runs harmless — missed runs are caught up automatically
on the next firing.

Cadence (Windows Task Scheduler):
    Daily at 21:30 UTC (~16:30 ET, 30 min post-close to let Schwab settle).
    On weekends, runs are no-ops (Schwab returns Friday's bar already in DB).

Usage:
    python scripts/us/equities/schwab/us_equities_schwab_eod.py
    python scripts/us/equities/schwab/us_equities_schwab_eod.py --days 14
    python scripts/us/equities/schwab/us_equities_schwab_eod.py --universe sp500
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Path layout: scripts/us/equities/schwab/<file>.py
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.countries.us.equities.schwab import get_client  # noqa: E402
from factorlab.countries.us.equities.schwab._jobs import (  # noqa: E402
    fetch_one,
    load_universe,
    seed_missing_instruments,
)
from factorlab.storage.db import get_engine  # noqa: E402
from factorlab.storage.ingest import write_candles  # noqa: E402

UNIVERSES_DIR = PROJECT_ROOT / "configs" / "universes"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s -- %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"us_equities_schwab_eod_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log",
            encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("us_equities_schwab_eod")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Schwab US EOD daily catch-up cron")
    p.add_argument("--universe", default="russell3000",
                   help="Universe short name (default: russell3000)")
    p.add_argument("--days", type=int, default=7,
                   help="Lookback window in days (default 7 — overlap for resilience)")
    p.add_argument("--workers", type=int, default=4,
                   help="Concurrent symbol fetches (default 4)")
    p.add_argument("--limit", type=int, help="Cap symbols (testing)")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write to DB; just fetch and report")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    log.info("=" * 60)
    log.info("Schwab US EOD catch-up -- universe=%s days=%d", args.universe, args.days)
    log.info("=" * 60)

    universe_path = UNIVERSES_DIR / f"us_{args.universe}.yaml"
    symbols = load_universe(universe_path)
    if args.limit:
        symbols = symbols[:args.limit]
    log.info("Universe: %d symbols", len(symbols))

    client = get_client(interactive=False)
    engine = get_engine()

    inst_lookup = seed_missing_instruments(client, engine, symbols)
    resolved = [s for s in symbols if s in inst_lookup]
    skipped = [s for s in symbols if s not in inst_lookup]
    if skipped:
        log.warning("Skipping %d unresolved symbols: %s",
                    len(skipped), skipped[:5] + (["..."] if len(skipped) > 5 else []))

    now = datetime.now(timezone.utc)
    from_dt = now - timedelta(days=args.days)
    log.info("Window: %s -> %s (UTC)", from_dt.date(), now.date())
    log.info("Fetching 1d bars for %d symbols (workers=%d) ...", len(resolved), args.workers)

    total_bars = 0
    total_inserted = 0
    empty_syms = 0
    failures: list[tuple[str, str]] = []
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(fetch_one, client, sym, "1d", from_dt=from_dt, to_dt=now): sym
            for sym in resolved
        }
        for i, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                df = fut.result()
                if df is None or df.empty:
                    empty_syms += 1
                    continue

                total_bars += len(df)
                if args.dry_run:
                    if i % 250 == 0 or i == len(resolved):
                        log.info("[%d/%d] %s: %d bars (dry-run)", i, len(resolved), sym, len(df))
                    continue

                n = write_candles(
                    df, inst_lookup[sym], None,
                    source="schwab_pricehistory",
                    engine=engine,
                    frequency="1d",
                )
                total_inserted += n
                if i % 250 == 0 or i == len(resolved):
                    log.info("[%d/%d] %s: %d bars -> %d inserted",
                             i, len(resolved), sym, len(df), n)
            except Exception as e:
                log.error("[%d/%d] %s: %s", i, len(resolved), sym, str(e)[:200])
                failures.append((sym, str(e)[:200]))

    elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info("Done in %.1f s -- %d symbols, %d empty, %d bars fetched, %d rows inserted, %d failures",
             elapsed, len(resolved), empty_syms, total_bars, total_inserted, len(failures))
    if failures:
        log.warning("First 10 failures:")
        for sym, err in failures[:10]:
            log.warning("  %s -> %s", sym, err)
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
