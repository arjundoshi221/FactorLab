"""india_equities_upstox_live — Live 5-min intraday candle poller for India equities + futures.

Pattern:    country_domain_vendor_action
Country:    India (NSE; BSE pending)
Domain:     equities (cash + nearest-expiry futures)
Vendor:     Upstox V3 (OAuth code-grant, daily token rotation)
Action:     live (long-running session daemon)

Run model:  Long-running process with internal sleep loop. NOT suitable for
            Task Scheduler past start — supervisor or `--daemon` must keep alive.
Schedule:   Task Scheduler `FactorLab-IndiaEquities-Upstox-Live`, daily 03:40 UTC.
Duration:   ~6h (03:40-10:05 UTC = 09:10-15:35 IST).
Cadence:    equities every 5 min · nearest-expiry futures every 10 min (staggered).
Rate use:   ~1,800/2,000 Upstox calls per 30 min at fo_eligible (~200 symbols).
Output:     Postgres market_in.fact_equity_intraday (canonical) + Arrow IPC cache.
Health:     Hourly "session nominal" email by default; --health-interval seconds (0 disables).
Failure:    factorlab.shared.notify.notify(severity='fatal') on uncaught crash via supervised().

Staggered polling (rate limit: 2,000 calls per 30 min):
  - Equity candles:  every 5 min  → 200 x 6 polls/30min = 1,200
  - Futures candles: every 10 min → 200 x 3 polls/30min =   600
                                         Total: 1,800 / 2,000 = 90%

Usage:
  python scripts/in/equities/upstox/india_equities_upstox_live.py --universe demo                  # one session, then exit
  python scripts/in/equities/upstox/india_equities_upstox_live.py --universe nifty500 --daemon
  python scripts/in/equities/upstox/india_equities_upstox_live.py --universe fo_eligible --daemon
  python scripts/in/equities/upstox/india_equities_upstox_live.py --universe nifty500 --daemon --health-interval 1800

Docs:
  docs/data-sources/india/upstox.md          — vendor integration
  docs/operations/orchestrators.md            — schedule + failure modes
  docs/operations/windows-task-scheduler.md  — Task Scheduler registration
"""

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

import pandas as pd
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")

# Path layout: scripts/in/equities/upstox/<file>.py
#   parents[0]=upstox, [1]=equities, [2]=in, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.shared.paths import raw_dir  # noqa: E402
from factorlab.shared.runtime import (  # noqa: E402
    ExitCode,
    GracefulShutdown,
    HealthReporter,
    Heartbeat,
    MarketWindow,
    WatermarkTracker,
    setup_logging,
    supervised,
)
from factorlab.countries.in_.equities.upstox.auth import ensure_token, validate_token  # noqa: E402
from factorlab.countries.in_.equities.upstox.client import get_session  # noqa: E402
from factorlab.countries.in_.equities.upstox.instruments import (  # noqa: E402
    find_equities,
    find_nearest_future,
    load_or_download,
)
from factorlab.countries.in_.equities.upstox.universes import load_universe  # noqa: E402
from factorlab.storage.db import get_engine  # noqa: E402
from factorlab.storage.ingest import sync_contracts, sync_instruments, write_candles  # noqa: E402

log = setup_logging("india_equities_upstox_live")

# ── Config ───────────────────────────────────────────────────────────────────
MARKET = MarketWindow(
    calendar_key="XBOM",
    open_time=dt_time(9, 15),     # 09:15 IST = 03:45 UTC
    close_time=dt_time(15, 30),   # 15:30 IST = 10:00 UTC
    tz=IST,
    pre_open_min=5,               # start at 09:10 IST
)

INSTRUMENTS_CACHE_DIR = raw_dir("upstox_instruments")
LIVE_OUTPUT_DIR = raw_dir("upstox_live")
SLEEP_BETWEEN_CALLS = 0.05
POLL_INTERVAL_SECONDS = 300  # 5 minutes
FUT_POLL_INTERVAL = 600  # 10 minutes — staggered to stay under rate limit
TOKEN_REVALIDATE_INTERVAL = 1800  # 30 minutes

INTRADAY_URL = "https://api.upstox.com/v3/historical-candle/intraday/{key}/minutes/1"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]

shutdown = GracefulShutdown(log)
shutdown.install()


# ── Data fetch ───────────────────────────────────────────────────────────────


def fetch_intraday(sess, instrument_key: str) -> pd.DataFrame | None:
    """Fetch today's intraday 1-min candles for *instrument_key*."""
    # URL-encode pipe in FUT keys (e.g. NSE_FO|67003 -> NSE_FO%7C67003)
    url = INTRADAY_URL.format(key=instrument_key.replace("|", "%7C"))
    resp = sess.get(url, timeout=15)

    if resp.status_code == 401:
        log.error("401 — token expired")
        return None
    if resp.status_code != 200:
        log.warning("HTTP %d for %s", resp.status_code, instrument_key)
        return None

    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        return None

    df = pd.DataFrame(candles, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def _save_candles(
    df: pd.DataFrame,
    symbol: str,
    suffix: str,
    today: str,
    watermarks: WatermarkTracker,
) -> int:
    """Save candles to Arrow IPC (.arrow) with dedup. Returns count of new candles."""
    wm_key = f"{symbol}_{suffix}"
    new_df = watermarks.filter(df, key=wm_key, time_col="timestamp")

    if new_df.empty:
        return 0

    out_dir = LIVE_OUTPUT_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}_{suffix}.arrow"

    # Atomic write — tmp file + os.replace. Guards against corruption when the
    # daemon is killed mid-write (SIGTERM at session end, machine sleep, etc.).
    # If existing file is corrupt (e.g. legacy from a pre-atomic crash), discard
    # and rewrite from this poll's data.
    existing = None
    if out_path.exists():
        try:
            existing = pd.read_feather(out_path)
        except Exception as exc:
            log.warning("Discarding corrupt feather %s: %s — rewriting", out_path.name, exc)
    if existing is not None:
        merged = pd.concat([existing, new_df]).drop_duplicates(subset="timestamp").sort_values("timestamp")
    else:
        merged = new_df.sort_values("timestamp")

    tmp_path = out_path.with_suffix(out_path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
    merged.reset_index(drop=True).to_feather(tmp_path)
    os.replace(tmp_path, out_path)
    watermarks.mark(df, key=wm_key, time_col="timestamp")
    return len(new_df)


# ── Polling loop ─────────────────────────────────────────────────────────────


def poll_once(
    sess,
    symbols: list[str],
    eq_lookup: dict[str, dict],
    instruments: list[dict],
    today: str,
    watermarks: WatermarkTracker,
    *,
    fetch_futures: bool = False,
    db_engine=None,
    inst_lookup: dict[str, object] | None = None,
    contract_lookup: dict[str, object] | None = None,
) -> tuple[int, int]:
    """Run one polling sweep. Returns (symbols_fetched, new_candles).

    Writes to DB first (canonical), then Arrow IPC cache (secondary).
    When *fetch_futures* is True, also fetches nearest-expiry FUT candles
    for each symbol that has an F&O contract.
    """
    total_new = 0
    fetched = 0

    for symbol in symbols:
        if shutdown.triggered:
            break

        rec = eq_lookup.get(symbol)
        if not rec:
            continue

        # ── Equity candles (always) ──
        df = fetch_intraday(sess, rec["instrument_key"])
        if df is not None and not df.empty:
            # DB write (canonical)
            inst_id = inst_lookup.get(symbol) if inst_lookup else None
            if inst_id and db_engine:
                write_candles(df, inst_id, None, "upstox", db_engine)

            # Arrow IPC cache (secondary)
            new = _save_candles(df, symbol, "1min", today, watermarks)
            total_new += new
            fetched += 1
        time.sleep(SLEEP_BETWEEN_CALLS)

        # ── Futures candles (staggered) ──
        if fetch_futures and not shutdown.triggered:
            fut = find_nearest_future(instruments, symbol)
            if fut:
                fut_df = fetch_intraday(sess, fut["instrument_key"])
                if fut_df is not None and not fut_df.empty:
                    # DB write (canonical)
                    inst_id = inst_lookup.get(symbol) if inst_lookup else None
                    contract_id = contract_lookup.get(fut["instrument_key"]) if contract_lookup else None
                    if inst_id and contract_id and db_engine:
                        write_candles(fut_df, inst_id, contract_id, "upstox", db_engine)

                    # Arrow IPC cache (secondary)
                    new = _save_candles(fut_df, symbol, "fut_1min", today, watermarks)
                    total_new += new
                time.sleep(SLEEP_BETWEEN_CALLS)

    return fetched, total_new


def now_utc() -> datetime:
    return datetime.now(UTC)


# Market open/close in UTC for the wait/end-of-day comparisons below.
# (We need UTC times to compare against now_utc(); MarketWindow gives us
# session validation + the IST view of poll_start.)
MARKET_OPEN_UTC = dt_time(3, 45)    # 09:15 IST
MARKET_CLOSE_UTC = dt_time(10, 0)   # 15:30 IST
PRE_OPEN_UTC = dt_time(3, 40)       # 09:10 IST


def main() -> int:
    parser = argparse.ArgumentParser(description="India 5-min live candle poller")
    parser.add_argument("--universe", default="demo", help="Universe name from india.yaml")
    parser.add_argument("--daemon", action="store_true", help="Keep alive across trading days")
    parser.add_argument(
        "--health-interval",
        type=int,
        default=3600,
        help="Seconds between 'session nominal' health emails. 0 disables. Default 3600 (1h).",
    )
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("india_equities_upstox_live starting — universe=%s daemon=%s", args.universe, args.daemon)
    log.info("=" * 60)

    # Auth
    token = ensure_token(interactive=False)
    sess = get_session(token)

    # Load universe + instruments
    symbols = load_universe(args.universe, PROJECT_ROOT)
    log.info("Universe '%s': %d symbols", args.universe, len(symbols))

    instruments = load_or_download("NSE", INSTRUMENTS_CACHE_DIR)
    eq_lookup = find_equities(instruments)

    # DB sync: instruments + contracts
    db_engine = get_engine()
    inst_lookup = sync_instruments(instruments, db_engine)
    log.info("DB sync: %d instruments", len(inst_lookup))
    contract_lookup = sync_contracts(instruments, inst_lookup, db_engine)
    log.info("DB sync: %d contracts", len(contract_lookup))

    # Watermarks: tracker keyed by {symbol}_{suffix}.
    watermarks = WatermarkTracker()
    last_token_check = time.monotonic()
    last_fut_poll = 0.0  # monotonic timestamp of last futures poll
    heartbeat = Heartbeat("india_equities_upstox_live")
    health = HealthReporter(
        source="india_equities_upstox_live",
        script="scripts/in/equities/upstox/india_equities_upstox_live.py",
        frequency="5-min session (03:40-10:05 UTC)",
        vendor="Upstox",
        domain="equities",
        country="IN",
        interval_seconds=args.health_interval,
    )

    while not shutdown.triggered:
        heartbeat.tick()
        now = now_utc()
        # During market hours (03:40-10:00 UTC), UTC date == IST date
        today = now.strftime("%Y-%m-%d")

        # Check if trading day
        if not MARKET.is_trading_day():
            if args.daemon:
                log.info("Not a trading day — sleeping 1 hour")
                time.sleep(3600)
                continue
            else:
                log.info("Not a trading day. Exiting.")
                return int(ExitCode.NOT_TRADING_DAY)

        # Wait for market open
        if now.time() < MARKET_OPEN_UTC:
            wait_sec = (
                datetime.combine(now.date(), MARKET_OPEN_UTC, tzinfo=UTC) - now
            ).total_seconds()
            log.info("Waiting %.0f seconds for market open (03:45 UTC)", wait_sec)
            time.sleep(max(wait_sec, 1))
            continue

        # Market closed for today
        if now.time() > MARKET_CLOSE_UTC:
            # Final sweep (include futures)
            log.info("Market closed — running final sweep")
            fetched, new = poll_once(
                sess, symbols, eq_lookup, instruments, today, watermarks,
                fetch_futures=True,
                db_engine=db_engine, inst_lookup=inst_lookup, contract_lookup=contract_lookup,
            )
            log.info("Final sweep: %d symbols, %d new candles", fetched, new)
            health.record_sweep(fetched=fetched, new_bars=new, total_symbols=len(symbols))
            health.force_report(reason="market close (15:30 IST / 10:00 UTC)")

            if args.daemon:
                tomorrow_open = datetime.combine(
                    now.date() + timedelta(days=1),
                    PRE_OPEN_UTC,
                    tzinfo=UTC,
                )
                sleep_sec = (tomorrow_open - now).total_seconds()
                log.info("Daemon mode — sleeping %.0f seconds until tomorrow 03:40 UTC", sleep_sec)
                watermarks.clear()
                health.reset_day()
                time.sleep(max(sleep_sec, 1))
                # Refresh token + instruments + DB sync for new day
                token = ensure_token(interactive=False)
                sess = get_session(token)
                instruments = load_or_download("NSE", INSTRUMENTS_CACHE_DIR)
                eq_lookup = find_equities(instruments)
                inst_lookup = sync_instruments(instruments, db_engine)
                contract_lookup = sync_contracts(instruments, inst_lookup, db_engine)
                continue
            else:
                log.info("Market closed. Session complete.")
                return int(ExitCode.OK)

        # ── Poll ─────────────────────────────────────────────────────────
        # Staggered: equity every 5 min, futures every 10 min
        mono_now = time.monotonic()
        do_futures = (mono_now - last_fut_poll) >= FUT_POLL_INTERVAL

        mode = "equity+futures" if do_futures else "equity"
        log.info("Polling %d symbols at %s UTC (%s)", len(symbols), now.strftime("%H:%M:%S"), mode)
        fetched, new = poll_once(
            sess, symbols, eq_lookup, instruments, today, watermarks,
            fetch_futures=do_futures,
            db_engine=db_engine, inst_lookup=inst_lookup, contract_lookup=contract_lookup,
        )
        log.info("  -> %d symbols fetched, %d new candles", fetched, new)
        health.record_sweep(fetched=fetched, new_bars=new, total_symbols=len(symbols))
        health.maybe_report()

        if do_futures:
            last_fut_poll = mono_now

        # Periodic token re-validation
        elapsed = time.monotonic() - last_token_check
        if elapsed > TOKEN_REVALIDATE_INTERVAL:
            try:
                validate_token(token)
                log.info("Token re-validated (every %ds)", TOKEN_REVALIDATE_INTERVAL)
            except Exception:
                log.warning("Token expired mid-session — re-authenticating")
                token = ensure_token(interactive=False)
                sess = get_session(token)
            last_token_check = time.monotonic()

        # Sleep until next poll
        sleep_target = POLL_INTERVAL_SECONDS
        if sleep_target > 0 and not shutdown.triggered:
            next_poll = now_utc() + timedelta(seconds=sleep_target)
            log.info("Next poll at %s UTC (sleeping %.0fs)", next_poll.strftime("%H:%M:%S"), sleep_target)
            time.sleep(sleep_target)

    log.info("Shutdown complete.")
    return int(ExitCode.OK)


if __name__ == "__main__":
    from factorlab.shared.notify import notify  # avoid import at module load

    sys.exit(supervised(
        main,
        name="india_equities_upstox_live",
        on_crash=lambda exc: notify(
            subject="india_equities_upstox_live crashed",
            body=f"{type(exc).__name__}: {exc}",
            severity="fatal",
            source="india_equities_upstox_live",
            script="scripts/in/equities/upstox/india_equities_upstox_live.py",
            frequency="5-min session (03:40-10:05 UTC)",
            vendor="Upstox",
            domain="equities",
            country="IN",
        ),
    ))
