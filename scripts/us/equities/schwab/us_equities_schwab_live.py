#!/usr/bin/env python3
"""us_equities_schwab_live -- catch-up 1m ingest on hourly cron (no daemon).

This is the US "live" ingest, mirroring india_equities_upstox_live.py's role
but using a cron pattern instead of a long-running daemon: Schwab's 2 req/sec
rate cap makes per-minute daemon polling impractical for SP500-wide universe;
the cron catches a 2h window each firing instead, with idempotent upsert.

Run model: short-lived cron job, fired by Windows Task Scheduler every hour.
Pulls the last N hours of 1m bars (default 2h overlap for resilience) for the
SP500 universe and upserts into ``market_us.fact_equity``. The unique index
(instrument_id, freq_id, bar_time) makes overlap with prior runs harmless --
missed runs are caught up automatically on the next firing within Schwab's
48-day 1m lookback window.

Cadence (registered in Task Scheduler by owner, not by this script):
    Every hour, 24/7. Off-hours = cheap no-ops (Schwab returns empty bars).

Usage:
    python scripts/us/equities/schwab/us_equities_schwab_live.py
    python scripts/us/equities/schwab/us_equities_schwab_live.py --hours 6
    python scripts/us/equities/schwab/us_equities_schwab_live.py --universe watchlist
    python scripts/us/equities/schwab/us_equities_schwab_live.py --workers 2
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Path layout: scripts/us/equities/schwab/<file>.py
#   parents[0]=schwab, [1]=equities, [2]=us, [3]=scripts, [4]=repo root
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
            LOG_DIR / f"us_equities_schwab_live_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log",
            encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("us_equities_schwab_live")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Schwab US live 1m catch-up cron")
    p.add_argument("--universe", default="sp500",
                   help="Universe short name (default: sp500)")
    p.add_argument("--hours", type=int, default=2,
                   help="Lookback window in hours (default 2 -- overlap for resilience)")
    p.add_argument("--workers", type=int, default=4,
                   help="Concurrent symbol fetches (default 4)")
    p.add_argument("--limit", type=int, help="Cap symbols (testing)")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write to DB; just fetch and report")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    log.info("=" * 60)
    log.info("Schwab US live catch-up -- universe=%s hours=%d", args.universe, args.hours)
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
    from_dt = now - timedelta(hours=args.hours)
    to_dt = now
    log.info("Window: %s -> %s (UTC)", from_dt.isoformat(timespec="minutes"),
             to_dt.isoformat(timespec="minutes"))
    log.info("Fetching 1m bars for %d symbols (workers=%d) ...", len(resolved), args.workers)

    total_bars = 0
    total_inserted = 0
    empty_syms = 0
    failures: list[tuple[str, str]] = []
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(fetch_one, client, sym, "1m", from_dt=from_dt, to_dt=to_dt): sym
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
                    if i % 100 == 0 or i == len(resolved):
                        log.info("[%d/%d] %s: %d bars (dry-run)", i, len(resolved), sym, len(df))
                    continue

                n = write_candles(
                    df, inst_lookup[sym], None,
                    source="schwab_pricehistory",
                    engine=engine,
                    frequency="1m",
                )
                total_inserted += n
                if i % 100 == 0 or i == len(resolved):
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
