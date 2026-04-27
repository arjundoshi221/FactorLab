"""factlab_india_hourly — Hourly 1-min candle backfill during market hours.

Schedule: Windows Task Scheduler at 10:15, 11:15, 12:15, 13:15, 14:15, 15:15, 15:28 IST.
Purpose:  Fetch today's 1-minute candles for the configured universe.

Each run fetches the full day's candles (from_date=today, to_date=today),
deduplicates against existing data, and writes per-symbol Parquet files.

Usage:
  python scripts/factlab_india_hourly.py                    # default 'demo' universe
  python scripts/factlab_india_hourly.py --universe nifty50
  python scripts/factlab_india_hourly.py --universe nifty100 --force
  python scripts/factlab_india_hourly.py --universe nifty100 --force
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import exchange_calendars as xcals  # noqa: E402

from factorlab.sources.upstox.auth import ensure_token  # noqa: E402
from factorlab.sources.upstox.client import get_session  # noqa: E402
from factorlab.sources.upstox.instruments import find_equities, load_or_download  # noqa: E402
from factorlab.sources.upstox.universes import load_universe  # noqa: E402

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"factlab_india_hourly_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("factlab_india_hourly")

# ── Config ───────────────────────────────────────────────────────────────────
CALENDAR_KEY = "XBOM"
INSTRUMENTS_CACHE_DIR = PROJECT_ROOT / "data" / "upstox" / "instruments"
CANDLE_OUTPUT_DIR = PROJECT_ROOT / "data" / "in" / "candles"
SLEEP_BETWEEN_CALLS = 0.05

V3_URL = "https://api.upstox.com/v3/historical-candle/{key}/minutes/1/{to_date}/{from_date}"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]


def fetch_candles(
    sess, instrument_key: str, today: str,
) -> pd.DataFrame | None:
    """Fetch 1-min candles for *instrument_key* for today. Returns DataFrame or None."""
    url = V3_URL.format(key=instrument_key, to_date=today, from_date=today)
    resp = sess.get(url, timeout=15)

    if resp.status_code == 401:
        log.error("401 — token expired mid-session")
        return None
    if resp.status_code != 200:
        log.warning("HTTP %d for %s: %s", resp.status_code, instrument_key, resp.text[:120])
        return None

    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        return None

    df = pd.DataFrame(candles, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Filter to trading hours (09:15–15:29)
    t = df["timestamp"].dt.time
    mask = (t >= pd.Timestamp("09:15").time()) & (t <= pd.Timestamp("15:29").time())
    return df[mask].reset_index(drop=True)


def save_candles(df: pd.DataFrame, symbol: str, today: str) -> Path:
    """Save candles to ``data/in/candles/{date}/{symbol}_1min.parquet``."""
    out_dir = CANDLE_OUTPUT_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}_1min.parquet"

    # Merge with existing if file already present (dedup by timestamp)
    if out_path.exists():
        existing = pd.read_parquet(out_path)
        df = pd.concat([existing, df]).drop_duplicates(subset="timestamp").sort_values("timestamp")

    df.to_parquet(out_path, index=False)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="India hourly candle backfill")
    parser.add_argument("--universe", default="demo", help="Universe name from india.yaml")
    parser.add_argument("--force", action="store_true", help="Skip trading-day check")
    args = parser.parse_args()

    log.info("factlab_india_hourly starting — universe=%s", args.universe)

    # Trading day check
    if not args.force:
        cal = xcals.get_calendar(CALENDAR_KEY)
        today_str = datetime.now().strftime("%Y-%m-%d")
        if len(cal.sessions_in_range(today_str, today_str)) == 0:
            log.info("Not a trading day. Exit 10.")
            return 10

    # Auth
    token = ensure_token(interactive=False)
    sess = get_session(token)

    # Load universe + instruments
    symbols = load_universe(args.universe, PROJECT_ROOT)
    log.info("Universe '%s': %d symbols", args.universe, len(symbols))

    instruments = load_or_download("NSE", INSTRUMENTS_CACHE_DIR)
    eq_lookup = find_equities(instruments)

    today = datetime.now().strftime("%Y-%m-%d")
    fetched = 0
    errors = 0
    total_candles = 0

    for symbol in symbols:
        rec = eq_lookup.get(symbol)
        if not rec:
            log.warning("%s: not found in instruments master", symbol)
            errors += 1
            continue

        key = rec["instrument_key"]
        df = fetch_candles(sess, key, today)
        if df is None or df.empty:
            log.info("%s: no candles (holiday/pre-market?)", symbol)
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        out_path = save_candles(df, symbol, today)
        fetched += 1
        total_candles += len(df)
        log.info("%s: %d candles → %s", symbol, len(df), out_path.name)
        time.sleep(SLEEP_BETWEEN_CALLS)

    log.info(
        "Done: %d/%d symbols, %d total candles, %d errors",
        fetched, len(symbols), total_candles, errors,
    )
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        exit_code = 99
    sys.exit(exit_code)
