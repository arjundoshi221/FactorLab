"""factlab_india_5min — Long-running 5-minute poller for near-real-time 1-min candles.

Run model:  Long-running process (internal sleep loop).
Duration:   Alive from ~09:10 to ~15:35 IST, polls every 5 minutes.
Deployment: Local machine, Railway, or any always-on environment.

Staggered polling (rate limit: 2,000 calls per 30 min):
  - Equity candles: every 5 min
  - Futures candles: every 10 min (nearest-expiry FUT per F&O symbol)

Rate budget at fo_eligible (~200 symbols):
  - Equity:  200 x 6 polls/30min = 1,200
  - Futures: 200 x 3 polls/30min =   600
  - Total:   1,800/2,000 = 90%

Usage:
  python scripts/factlab_india_5min.py                        # demo, exit after market
  python scripts/factlab_india_5min.py --universe nifty50
  python scripts/factlab_india_5min.py --universe fo_eligible --daemon
  python scripts/factlab_india_5min.py --universe nifty50 --daemon
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import exchange_calendars as xcals  # noqa: E402

from factorlab.sources.upstox.auth import ensure_token, validate_token  # noqa: E402
from factorlab.sources.upstox.client import get_session  # noqa: E402
from factorlab.sources.upstox.instruments import (  # noqa: E402
    find_equities,
    find_nearest_future,
    load_or_download,
)
from factorlab.sources.upstox.universes import load_universe  # noqa: E402

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"factlab_india_5min_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("factlab_india_5min")

# ── Config ───────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")
CALENDAR_KEY = "XBOM"
INSTRUMENTS_CACHE_DIR = PROJECT_ROOT / "data" / "upstox" / "instruments"
LIVE_OUTPUT_DIR = PROJECT_ROOT / "data" / "in" / "live"
SLEEP_BETWEEN_CALLS = 0.05
POLL_INTERVAL_SECONDS = 300  # 5 minutes
FUT_POLL_INTERVAL = 600  # 10 minutes — staggered to stay under rate limit
TOKEN_REVALIDATE_INTERVAL = 1800  # 30 minutes

INTRADAY_URL = "https://api.upstox.com/v3/historical-candle/intraday/{key}/minutes/1"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]

MARKET_OPEN = datetime.strptime("09:15", "%H:%M").time()
MARKET_CLOSE = datetime.strptime("15:30", "%H:%M").time()

# Graceful shutdown flag
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Received signal %s — shutting down after current batch", signum)
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


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
    watermarks: dict[str, datetime],
) -> int:
    """Save candles to parquet with dedup. Returns count of new candles."""
    wm_key = f"{symbol}_{suffix}"
    last_seen = watermarks.get(wm_key)
    if last_seen:
        new_df = df[df["timestamp"] > last_seen]
    else:
        new_df = df

    if new_df.empty:
        return 0

    out_dir = LIVE_OUTPUT_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}_{suffix}.parquet"

    if out_path.exists():
        existing = pd.read_parquet(out_path)
        merged = pd.concat([existing, new_df]).drop_duplicates(subset="timestamp").sort_values("timestamp")
    else:
        merged = new_df.sort_values("timestamp")

    merged.to_parquet(out_path, index=False)
    watermarks[wm_key] = df["timestamp"].max()
    return len(new_df)


# ── Polling loop ─────────────────────────────────────────────────────────────


def poll_once(
    sess,
    symbols: list[str],
    eq_lookup: dict[str, dict],
    instruments: list[dict],
    today: str,
    watermarks: dict[str, datetime],
    *,
    fetch_futures: bool = False,
) -> tuple[int, int]:
    """Run one polling sweep. Returns (symbols_fetched, new_candles).

    When *fetch_futures* is True, also fetches nearest-expiry FUT candles
    for each symbol that has an F&O contract.
    """
    total_new = 0
    fetched = 0

    for symbol in symbols:
        if _shutdown:
            break

        rec = eq_lookup.get(symbol)
        if not rec:
            continue

        # ── Equity candles (always) ──
        df = fetch_intraday(sess, rec["instrument_key"])
        if df is not None and not df.empty:
            new = _save_candles(df, symbol, "1min", today, watermarks)
            total_new += new
            fetched += 1
        time.sleep(SLEEP_BETWEEN_CALLS)

        # ── Futures candles (staggered) ──
        if fetch_futures and not _shutdown:
            fut = find_nearest_future(instruments, symbol)
            if fut:
                fut_df = fetch_intraday(sess, fut["instrument_key"])
                if fut_df is not None and not fut_df.empty:
                    new = _save_candles(fut_df, symbol, "fut_1min", today, watermarks)
                    total_new += new
                time.sleep(SLEEP_BETWEEN_CALLS)

    return fetched, total_new


def now_ist() -> datetime:
    return datetime.now(IST)


def main() -> int:
    parser = argparse.ArgumentParser(description="India 5-min live candle poller")
    parser.add_argument("--universe", default="demo", help="Universe name from india.yaml")
    parser.add_argument("--daemon", action="store_true", help="Keep alive across trading days")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("factlab_india_5min starting — universe=%s daemon=%s", args.universe, args.daemon)
    log.info("=" * 60)

    # Auth
    token = ensure_token(interactive=False)
    sess = get_session(token)

    # Load universe + instruments
    symbols = load_universe(args.universe, PROJECT_ROOT)
    log.info("Universe '%s': %d symbols", args.universe, len(symbols))

    instruments = load_or_download("NSE", INSTRUMENTS_CACHE_DIR)
    eq_lookup = find_equities(instruments)

    # Watermarks: {symbol_suffix: last_seen_timestamp}
    watermarks: dict[str, datetime] = {}
    last_token_check = time.monotonic()
    last_fut_poll = 0.0  # monotonic timestamp of last futures poll

    while not _shutdown:
        now = now_ist()
        today = now.strftime("%Y-%m-%d")

        # Check if trading day
        cal = xcals.get_calendar(CALENDAR_KEY)
        if len(cal.sessions_in_range(today, today)) == 0:
            if args.daemon:
                log.info("Not a trading day — sleeping 1 hour")
                time.sleep(3600)
                continue
            else:
                log.info("Not a trading day. Exiting.")
                return 10

        # Wait for market open
        if now.time() < MARKET_OPEN:
            wait_sec = (
                datetime.combine(now.date(), MARKET_OPEN, tzinfo=IST) - now
            ).total_seconds()
            log.info("Waiting %.0f seconds for market open (09:15 IST)", wait_sec)
            time.sleep(max(wait_sec, 1))
            continue

        # Market closed for today
        if now.time() > MARKET_CLOSE:
            # Final sweep (include futures)
            log.info("Market closed — running final sweep")
            fetched, new = poll_once(
                sess, symbols, eq_lookup, instruments, today, watermarks,
                fetch_futures=True,
            )
            log.info("Final sweep: %d symbols, %d new candles", fetched, new)

            if args.daemon:
                tomorrow_open = datetime.combine(
                    now.date() + timedelta(days=1),
                    datetime.strptime("09:10", "%H:%M").time(),
                    tzinfo=IST,
                )
                sleep_sec = (tomorrow_open - now).total_seconds()
                log.info("Daemon mode — sleeping %.0f seconds until tomorrow 09:10", sleep_sec)
                watermarks.clear()
                time.sleep(max(sleep_sec, 1))
                # Refresh token + instruments for new day
                token = ensure_token(interactive=False)
                sess = get_session(token)
                instruments = load_or_download("NSE", INSTRUMENTS_CACHE_DIR)
                eq_lookup = find_equities(instruments)
                continue
            else:
                log.info("Market closed. Session complete.")
                return 0

        # ── Poll ─────────────────────────────────────────────────────────
        # Staggered: equity every 5 min, futures every 10 min
        mono_now = time.monotonic()
        do_futures = (mono_now - last_fut_poll) >= FUT_POLL_INTERVAL

        mode = "equity+futures" if do_futures else "equity"
        log.info("Polling %d symbols at %s (%s)", len(symbols), now.strftime("%H:%M:%S"), mode)
        fetched, new = poll_once(
            sess, symbols, eq_lookup, instruments, today, watermarks,
            fetch_futures=do_futures,
        )
        log.info("  -> %d symbols fetched, %d new candles", fetched, new)

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
        if sleep_target > 0 and not _shutdown:
            next_poll = now_ist() + timedelta(seconds=sleep_target)
            log.info("Next poll at %s (sleeping %.0fs)", next_poll.strftime("%H:%M:%S"), sleep_target)
            time.sleep(sleep_target)

    log.info("Shutdown complete.")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        exit_code = 99
    sys.exit(exit_code)
