"""india_equities_upstox_historical — One-shot historical OHLCV backfill via Upstox V3.

Pattern:    country_domain_vendor_action
Country:    India (NSE; BSE pending)
Domain:     equities (cash + nearest-expiry futures)
Vendor:     Upstox V3 historical-candle API
Action:     historical (one-shot, manual trigger)

Run model:  One-shot. Pools per-symbol fetches across N workers. Idempotent
            against Postgres (upsert on (instrument_id, bar_time, frequency)).
Schedule:   Manual — no Task Scheduler entry. Typical cadence: quarterly for
            universe re-seed, monthly for gap-fill, ad-hoc for new universes.
Duration:   Depends heavily on (universe × intervals × date range × workers).
            nifty500 × 1d × 15yr × 4 workers ≈ 20-30 min on a clean run.
Output:     Postgres market_in.fact_equity_daily / market_in.fact_equity_intraday
            (canonical) + Parquet at data/upstox/historical/<symbol>_<interval>.parquet
            (analytical).
Health:     HealthReporter info-email on completion (`reason='backfill complete'`).
Failure:    notify(severity='fail') on per-symbol vendor errors (warned, continued);
            severity='fatal' on uncaught crash via supervised().

Intervals (v1):
  1d   — daily bars; Upstox supports back ~20 years (since ~2005)
  30m  — 30-minute bars; Upstox lookback ~1600 days (~4.4 years, probed 2026-06-15)
  5m   — 5-minute bars;  Upstox lookback ~1600 days (~4.4 years, probed 2026-06-15)
  1m   — 1-minute bars;  Upstox lookback ~1600 days (~4.4 years, probed 2026-06-15)
  15m queued for v2. Use intraday/live for live capture.

Products (v1):
  equities  — NSE_EQ instrument type EQ
  futures   — nearest-expiry FUT per F&O-eligible equity (no all-expiries in v1)

Idempotency:
  - Postgres: write_candles upserts on (instrument_id, bar_time, frequency)
  - Parquet: overwrites per (symbol, interval) file each run
  - Safe to re-run; re-run produces 0 new Postgres rows for unchanged windows.

Usage:
  # Default: daily bars for nifty500 equities back to 2010
  python scripts/in/equities/upstox/india_equities_upstox_historical.py --universe nifty500

  # Multi-interval, equities + nearest-expiry futures
  python scripts/in/equities/upstox/india_equities_upstox_historical.py \\
      --universe fo_eligible \\
      --products equities,futures \\
      --intervals 1d,30m \\
      --from-date 2020-01-01

  # Dry run — plans the work, makes zero API/DB calls
  python scripts/in/equities/upstox/india_equities_upstox_historical.py --universe demo --dry-run

Docs:
  docs/data-sources/india/upstox.md                            — vendor integration
  docs/developments/008-script-naming-india-historical.md       — design rationale
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

# ── Project root ─────────────────────────────────────────────────────────────
# Path layout: scripts/in/equities/upstox/<file>.py
#   parents[0]=upstox, [1]=equities, [2]=in, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

from factorlab.shared.paths import raw_dir  # noqa: E402
from factorlab.shared.runtime import (  # noqa: E402
    ExitCode,
    HealthReporter,
    setup_logging,
    supervised,
)
from factorlab.countries.in_.equities.upstox.auth import ensure_token  # noqa: E402
from factorlab.countries.in_.equities.upstox.client import get_session  # noqa: E402
from factorlab.countries.in_.equities.upstox.instruments import (  # noqa: E402
    find_equities,
    find_nearest_future,
    load_or_download,
)
from factorlab.countries.in_.equities.upstox.universes import load_universe  # noqa: E402
from factorlab.storage.db import get_engine  # noqa: E402
from factorlab.storage.ingest import (  # noqa: E402
    sync_contracts,
    sync_instruments,
    write_candles,
)

log = setup_logging("india_equities_upstox_historical")

INSTRUMENTS_CACHE_DIR = raw_dir("upstox_instruments")
HISTORICAL_DIR = raw_dir("upstox_historical")

# Upstox V3 historical endpoint shape:
#   https://api.upstox.com/v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
HISTORICAL_URL = (
    "https://api.upstox.com/v3/historical-candle/"
    "{key}/{unit}/{interval}/{to_date}/{from_date}"
)
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]

# Interval → (unit, interval-arg, max-window-days)
# Window limits per docs/data-sources/india/upstox.md §3 (V3 max range per call).
INTERVAL_MAP: dict[str, tuple[str, str, int]] = {
    # daily: max 1 decade per call (verified via UDAPI1148 on >10yr ranges)
    "1d":  ("days", "1", 365 * 10),
    # 30m: window into 90-day chunks to keep response size sane
    "30m": ("minutes", "30", 90),
    # 5m: ~75 bars per trading day; 30-day chunks ≲ 1.6k rows/call
    "5m":  ("minutes", "5", 30),
    # 1m: ~375 bars per trading day; 7-day chunks keep responses ≲ 3k rows/call
    "1m":  ("minutes", "1", 7),
}


# ── Plan ─────────────────────────────────────────────────────────────────────


def _per_interval_clamp(interval: str, from_dt: date) -> date:
    """Clamp from_date upward if it exceeds Upstox lookback for the interval."""
    today = date.today()
    if interval == "1d":
        floor = date(2005, 1, 1)
    elif interval in ("30m", "5m", "1m"):
        # Empirically probed 2026-06-15: Upstox V3 returns intraday candles
        # back to ~1600 days, empty beyond ~1625. Same cutoff for all 3.
        # See playground/explore/upstox/probe_intraday_lookback.py
        floor = today - timedelta(days=1600)
    else:
        floor = today - timedelta(days=365)
    return max(from_dt, floor)


def _date_windows(from_dt: date, to_dt: date, max_days: int) -> Iterable[tuple[date, date]]:
    """Split [from, to] into contiguous windows of ≤ max_days each."""
    cur = from_dt
    while cur <= to_dt:
        end = min(cur + timedelta(days=max_days - 1), to_dt)
        yield cur, end
        cur = end + timedelta(days=1)


def _build_plan(
    *,
    symbols: list[str],
    eq_lookup: dict[str, dict],
    instruments: list[dict],
    products: set[str],
    intervals: list[str],
    from_dt: date,
    to_dt: date,
) -> list[dict]:
    """Return a list of fetch jobs. Each job is one HTTP call."""
    jobs: list[dict] = []
    for sym in symbols:
        rec = eq_lookup.get(sym)
        if not rec:
            log.warning("Symbol %s not in equity lookup -- skipping", sym)
            continue

        targets: list[tuple[str, str]] = []  # (instrument_key, product_label)
        if "equities" in products:
            targets.append((rec["instrument_key"], "equity"))
        if "futures" in products:
            fut = find_nearest_future(instruments, sym)
            if fut is not None:
                targets.append((fut["instrument_key"], "future"))

        for inst_key, product in targets:
            for interval in intervals:
                clamped_from = _per_interval_clamp(interval, from_dt)
                _, _, max_days = INTERVAL_MAP[interval]
                for w_from, w_to in _date_windows(clamped_from, to_dt, max_days):
                    jobs.append({
                        "symbol": sym,
                        "instrument_key": inst_key,
                        "product": product,
                        "interval": interval,
                        "from": w_from,
                        "to": w_to,
                    })
    return jobs


# ── Fetch ────────────────────────────────────────────────────────────────────


class UpstoxAPIError(Exception):
    """Non-2xx response from Upstox after exhausting retries."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:120]}")
        self.status = status
        self.body = body


def _fetch_one(sess, job: dict, *, retry_429: int = 3) -> pd.DataFrame | None:
    """Run one historical-candle HTTP call.

    Returns parsed DataFrame on success (or None for empty 200 responses).
    Retries HTTP 429 with exponential backoff (2s, 4s, 8s).
    Raises UpstoxAPIError on any other non-2xx response, or on 429 after retries.
    """
    key = job["instrument_key"].replace("|", "%7C")
    unit, interval_arg, _ = INTERVAL_MAP[job["interval"]]
    url = HISTORICAL_URL.format(
        key=key,
        unit=unit,
        interval=interval_arg,
        to_date=job["to"].isoformat(),
        from_date=job["from"].isoformat(),
    )

    for attempt in range(retry_429 + 1):
        resp = sess.get(url, timeout=30)
        if resp.status_code == 429 and attempt < retry_429:
            backoff = 2 ** (attempt + 1)  # 2, 4, 8
            log.info("429 for %s %s — backoff %ds (attempt %d/%d)",
                     job["symbol"], job["interval"], backoff, attempt + 1, retry_429)
            time.sleep(backoff)
            continue
        break

    if resp.status_code != 200:
        raise UpstoxAPIError(resp.status_code, resp.text)

    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        return None

    df = pd.DataFrame(candles, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ── Write ────────────────────────────────────────────────────────────────────

# Per-path locks serialize parquet read-modify-write across workers. Different
# symbols still write in parallel (each path gets its own lock); two windows of
# the same symbol can never race. Combined with the tmp+os.replace below, this
# also guarantees no reader ever sees a half-written file.
_PARQUET_LOCKS: dict[Path, threading.Lock] = defaultdict(threading.Lock)
_PARQUET_LOCKS_GUARD = threading.Lock()


def _parquet_lock(path: Path) -> threading.Lock:
    with _PARQUET_LOCKS_GUARD:
        return _PARQUET_LOCKS[path]


def _write_one(
    df: pd.DataFrame,
    *,
    job: dict,
    inst_lookup: dict[str, object],
    contract_lookup: dict[str, object],
    engine,
    write_postgres: bool,
    write_parquet: bool,
) -> int:
    """Persist one fetch result. Returns rows inserted to Postgres (0 if disabled)."""
    written = 0

    if write_postgres:
        inst_id = inst_lookup.get(job["symbol"])
        contract_id = (
            contract_lookup.get(job["instrument_key"])
            if job["product"] == "future" else None
        )
        if inst_id is None:
            log.warning("No instrument_id for %s -- skipping Postgres write", job["symbol"])
        else:
            written = write_candles(
                df, inst_id, contract_id,
                source="upstox",
                engine=engine,
                frequency=job["interval"],
            )

    if write_parquet:
        out_dir = HISTORICAL_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = "fut" if job["product"] == "future" else "eq"
        out_path = out_dir / f"{job['symbol']}_{job['interval']}_{suffix}.parquet"

        # Serialize read-modify-write per output path (different symbols still
        # write in parallel). Then atomic-replace via tmp file so any concurrent
        # reader on this path either sees the old file or the new — never a
        # half-written one.
        with _parquet_lock(out_path):
            existing = None
            if out_path.exists():
                try:
                    existing = (
                        pd.read_feather(out_path) if out_path.suffix == ".arrow"
                        else pd.read_parquet(out_path)
                    )
                except Exception as exc:
                    # Corrupt existing file (e.g. leftover from an old racey run).
                    # Discard and rewrite fresh from this window's data.
                    log.warning("Discarding corrupt parquet %s: %s — rewriting", out_path.name, exc)
            if existing is not None:
                merged = (
                    pd.concat([existing, df])
                    .drop_duplicates(subset="timestamp")
                    .sort_values("timestamp")
                )
            else:
                merged = df
            tmp_path = out_path.with_suffix(out_path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
            merged.reset_index(drop=True).to_parquet(tmp_path, index=False)
            os.replace(tmp_path, out_path)

    return written


# ── Failure notify ──────────────────────────────────────────────────────────


def _failure_notify(message: str, *, subject: str, context: dict | None = None) -> None:
    """Route failure alert through factorlab.shared.notify with full metadata."""
    from factorlab.shared.notify import notify  # local import
    notify(
        subject=subject,
        body=message,
        severity="fail",
        source="india_equities_upstox_historical",
        script="scripts/in/equities/upstox/india_equities_upstox_historical.py",
        frequency="historical one-shot",
        vendor="Upstox",
        domain="equities",
        country="IN",
        context=context,
    )


# ── Main ────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Upstox V3 historical OHLCV backfill (India equities + nearest-expiry futures)"
    )
    p.add_argument("--universe", required=True,
                   help="Universe name from configs/universes/india.yaml")
    p.add_argument("--products", default="equities",
                   help="Comma list: equities, futures (default: equities)")
    p.add_argument("--intervals", default="1d",
                   help=f"Comma list of: {', '.join(INTERVAL_MAP.keys())} (default: 1d)")
    p.add_argument("--from-date", default="2010-01-01",
                   help="Earliest start YYYY-MM-DD (per-interval lookback clamps automatically). Default 2010-01-01.")
    p.add_argument("--to-date", default=None,
                   help="End date YYYY-MM-DD. Default: today (UTC).")
    p.add_argument("--write-postgres", action=argparse.BooleanOptionalAction, default=True,
                   help="Write to Postgres market_in.fact_equity_*. Default true.")
    p.add_argument("--write-parquet", action=argparse.BooleanOptionalAction, default=True,
                   help="Write per-symbol Parquet to data/upstox/historical/. Default true.")
    p.add_argument("--workers", type=int, default=4, help="Parallel symbol workers. Default 4.")
    p.add_argument("--sleep-per-call", type=float, default=0.08,
                   help="Seconds to sleep between job submissions. Default 0.08s "
                        "(~12 req/sec submission rate; per Upstox docs §2).")
    p.add_argument("--rate-budget-per-30min", type=int, default=1800,
                   help="Soft cap (leaves 200 buffer under Upstox 2000/30min). Default 1800.")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan and exit; no API or DB calls.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(find_dotenv(usecwd=True))

    log.info("=" * 60)
    log.info("india_equities_upstox_historical starting")
    log.info("  universe=%s products=%s intervals=%s",
             args.universe, args.products, args.intervals)
    log.info("  from=%s to=%s workers=%d dry_run=%s",
             args.from_date, args.to_date or "today", args.workers, args.dry_run)
    log.info("=" * 60)

    products = {p.strip() for p in args.products.split(",") if p.strip()}
    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    bad_products = products - {"equities", "futures"}
    if bad_products:
        log.error("Unknown products: %s. Allowed: equities, futures.", bad_products)
        return int(ExitCode.FATAL)
    bad_intervals = set(intervals) - set(INTERVAL_MAP.keys())
    if bad_intervals:
        log.error("Unknown intervals: %s. Allowed: %s",
                  bad_intervals, list(INTERVAL_MAP.keys()))
        return int(ExitCode.FATAL)

    from_dt = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    to_dt = (
        datetime.strptime(args.to_date, "%Y-%m-%d").date()
        if args.to_date else date.today()
    )
    if from_dt > to_dt:
        log.error("--from-date (%s) is after --to-date (%s)", from_dt, to_dt)
        return int(ExitCode.FATAL)

    # Universe + instruments
    symbols = load_universe(args.universe, PROJECT_ROOT)
    log.info("Universe '%s': %d symbols", args.universe, len(symbols))
    instruments = load_or_download("NSE", INSTRUMENTS_CACHE_DIR)
    eq_lookup = find_equities(instruments)

    # Build the plan up-front (so --dry-run can show it)
    jobs = _build_plan(
        symbols=symbols,
        eq_lookup=eq_lookup,
        instruments=instruments,
        products=products,
        intervals=intervals,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    log.info("Plan: %d total HTTP calls across %d symbols × %d intervals × %d products",
             len(jobs), len(symbols), len(intervals), len(products))

    if args.dry_run:
        log.info("--dry-run: planned %d calls. Sample (first 5):", len(jobs))
        for j in jobs[:5]:
            log.info("  %s %s %s [%s..%s]",
                     j["symbol"], j["product"], j["interval"], j["from"], j["to"])
        log.info("Exiting without API/DB calls.")
        return int(ExitCode.OK)

    # Real work
    token = ensure_token(interactive=False)
    sess = get_session(token)
    engine = get_engine() if args.write_postgres else None

    inst_lookup: dict[str, object] = {}
    contract_lookup: dict[str, object] = {}
    if args.write_postgres:
        inst_lookup = sync_instruments(instruments, engine)
        log.info("DB sync: %d instruments", len(inst_lookup))
        if "futures" in products:
            contract_lookup = sync_contracts(instruments, inst_lookup, engine)
            log.info("DB sync: %d contracts", len(contract_lookup))

    health = HealthReporter(
        source="india_equities_upstox_historical",
        script="scripts/in/equities/upstox/india_equities_upstox_historical.py",
        frequency="historical one-shot",
        vendor="Upstox",
        domain="equities",
        country="IN",
        interval_seconds=0,  # disable periodic; one final force_report at end
    )

    total_rows = 0
    failures = 0
    rate_window_start = time.monotonic()
    rate_window_calls = 0

    def _maybe_throttle():
        nonlocal rate_window_start, rate_window_calls
        # Per-call sleep — keeps submissions under Upstox's 50 req/sec and
        # Cloudflare's per-IP burst cap (see docs/data-sources/india/upstox.md §2).
        if args.sleep_per_call > 0:
            time.sleep(args.sleep_per_call)
        rate_window_calls += 1
        elapsed = time.monotonic() - rate_window_start
        if elapsed >= 1800:  # new 30-min window
            rate_window_start = time.monotonic()
            rate_window_calls = 0
            return
        if rate_window_calls >= args.rate_budget_per_30min:
            sleep_for = 1800 - elapsed
            log.warning("Hit rate budget (%d calls in %.0fs). Sleeping %.0fs.",
                        rate_window_calls, elapsed, sleep_for)
            time.sleep(sleep_for)
            rate_window_start = time.monotonic()
            rate_window_calls = 0

    def _process_job(job: dict) -> tuple[int, int]:
        """Return (rows_inserted, failures)."""
        try:
            df = _fetch_one(sess, job)
        except UpstoxAPIError as exc:
            log.warning("API error for %s %s [%s..%s]: %s",
                        job["symbol"], job["interval"], job["from"], job["to"], exc)
            return 0, 1
        except Exception as exc:
            log.warning("Fetch raised for %s %s: %s", job["symbol"], job["interval"], exc)
            return 0, 1
        if df is None or df.empty:
            return 0, 0
        try:
            inserted = _write_one(
                df, job=job,
                inst_lookup=inst_lookup,
                contract_lookup=contract_lookup,
                engine=engine,
                write_postgres=args.write_postgres,
                write_parquet=args.write_parquet,
            )
        except Exception as exc:
            log.warning("Write raised for %s %s: %s", job["symbol"], job["interval"], exc)
            return 0, 1
        return inserted, 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures_iter = []
        for job in jobs:
            _maybe_throttle()
            futures_iter.append(pool.submit(_process_job, job))
        for done_count, fut in enumerate(as_completed(futures_iter), 1):
            inserted, fail = fut.result()
            total_rows += inserted
            failures += fail
            health.record_sweep(
                fetched=1 - fail, new_bars=inserted, total_symbols=1,
            )
            if done_count % 50 == 0 or done_count == len(jobs):
                log.info("Progress: %d/%d done · %d rows · %d failures",
                         done_count, len(jobs), total_rows, failures)

    health.force_report(reason=f"backfill complete ({len(jobs)} calls)")

    log.info("=" * 60)
    log.info("Historical backfill complete: %d rows inserted, %d failures",
             total_rows, failures)
    log.info("=" * 60)

    if failures > 0 and failures >= max(1, len(jobs) // 10):
        # >10% failure rate is meaningful — send a fail email on top of the info one
        _failure_notify(
            message=f"Historical backfill finished with {failures}/{len(jobs)} failures "
                    f"({failures / len(jobs) * 100:.1f}%). See logs for symbol-level detail.",
            subject="Historical backfill — high failure rate",
            context={
                "total_jobs": len(jobs),
                "failures": failures,
                "rows_inserted": total_rows,
                "universe": args.universe,
                "intervals": ",".join(intervals),
            },
        )
        return int(ExitCode.WARN)

    return int(ExitCode.OK)


if __name__ == "__main__":
    from factorlab.shared.notify import notify  # avoid module-load cycle

    sys.exit(supervised(
        main,
        name="india_equities_upstox_historical",
        on_crash=lambda exc: notify(
            subject="india_equities_upstox_historical crashed",
            body=f"{type(exc).__name__}: {exc}",
            severity="fatal",
            source="india_equities_upstox_historical",
            script="scripts/in/equities/upstox/india_equities_upstox_historical.py",
            frequency="historical one-shot",
            vendor="Upstox",
            domain="equities",
            country="IN",
        ),
    ))
