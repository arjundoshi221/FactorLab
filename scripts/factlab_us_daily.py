#!/usr/bin/env python3
"""FactorLab US Daily — EODHD demo pipeline.

Fetches daily OHLCV bars for US equities via EODHD, prints a summary,
and saves Parquet files to data/eodhd/.

Usage:
    python scripts/factlab_us_daily.py                  # demo: 4 free tickers
    python scripts/factlab_us_daily.py --symbols AAPL.US MSFT.US
    python scripts/factlab_us_daily.py --from-date 2025-01-01
    python scripts/factlab_us_daily.py --db              # also write to Postgres
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import find_dotenv, load_dotenv

# Ensure src/ is importable when running as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from factorlab.sources.eodhd.candles import fetch_daily_bars
from factorlab.sources.eodhd.client import EODHDClient

load_dotenv(find_dotenv(usecwd=True))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("factlab_us_daily")

DEMO_SYMBOLS = ["AAPL.US", "TSLA.US", "AMZN.US", "VTI.US"]
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "eodhd"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="EODHD US daily bar fetcher")
    p.add_argument("--symbols", nargs="+", default=None,
                   help="Symbols to fetch (EODHD format, e.g. AAPL.US). Default: demo tickers")
    p.add_argument("--from-date", default=None,
                   help="Start date YYYY-MM-DD (default: 1 year ago)")
    p.add_argument("--to-date", default=None,
                   help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--db", action="store_true",
                   help="Also write to Postgres (requires DATABASE_URL)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    symbols = args.symbols or DEMO_SYMBOLS

    api_key = os.getenv("EODHD_API_KEY", "demo")
    is_demo = api_key == "demo"
    if is_demo:
        log.info("Using demo API key — only AAPL.US, TSLA.US, AMZN.US, VTI.US work")
    else:
        log.info("Using EODHD API key (free tier: 20 calls/day)")

    client = EODHDClient(api_key=api_key)

    # ── Fetch bars ───────────────────────────────────────────────────────
    all_bars: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = fetch_daily_bars(client, sym, from_date=args.from_date, to_date=args.to_date)
            if not df.empty:
                all_bars[sym] = df
        except Exception as e:
            log.error("Failed %s: %s", sym, e)

    if not all_bars:
        log.error("No data fetched. Check API key and symbols.")
        sys.exit(1)

    # ── Print summary ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"  EODHD US Daily Bars — {len(all_bars)}/{len(symbols)} symbols fetched")
    print("=" * 70)

    summary_rows = []
    for sym, df in all_bars.items():
        last = df.iloc[-1]
        summary_rows.append({
            "Symbol": sym,
            "Bars": len(df),
            "From": str(df["trade_date"].iloc[0]),
            "To": str(df["trade_date"].iloc[-1]),
            "Last Close": f"${last['close']:.2f}",
            "Last Adj Close": f"${last['adj_close']:.2f}" if pd.notna(last.get("adj_close")) else "N/A",
            "Last Volume": f"{int(last['volume']):,}" if pd.notna(last["volume"]) else "N/A",
        })

    summary_df = pd.DataFrame(summary_rows)
    print(summary_df.to_string(index=False))

    # ── Save Parquet ─────────────────────────────────────────────────────
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for sym, df in all_bars.items():
        path = DATA_DIR / f"{sym.replace('.', '_')}.parquet"
        df.to_parquet(path, index=False)
        log.info("Saved %s → %s (%d rows)", sym, path.name, len(df))

    print(f"\nParquet files saved to: {DATA_DIR}")
    print(f"API calls used: {len(all_bars)} (budget: {'20/day' if not is_demo else 'unlimited for demo tickers'})")

    # ── Optional: write to Postgres ──────────────────────────────────────
    if args.db:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            log.error("--db requires DATABASE_URL in .env")
            sys.exit(1)

        from factorlab.storage.db import get_engine
        from factorlab.storage.ingest import write_candles_daily

        engine = get_engine(db_url)

        # Resolve instrument_ids — requires ref.instruments to be seeded
        from sqlalchemy import text as sql_text
        with engine.connect() as conn:
            for sym, df in all_bars.items():
                inst_key = sym  # EODHD instrument_key format: AAPL.US
                row = conn.execute(
                    sql_text("SELECT id FROM ref.instruments WHERE instrument_key = :key"),
                    {"key": inst_key},
                ).fetchone()
                if not row:
                    log.warning("Instrument %s not in ref.instruments — skipping DB write. "
                                "Run instrument sync first.", sym)
                    continue
                n = write_candles_daily(df, row[0], "eodhd", engine)
                log.info("DB: %s → %d rows inserted", sym, n)

        print("Postgres write complete.")


if __name__ == "__main__":
    main()
