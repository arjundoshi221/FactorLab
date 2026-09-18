#!/usr/bin/env python3
"""us_equities_schwab_historical -- one-shot historical backfill from Schwab.

Pulls daily / 5m / 1m / etc. bars for a US universe and writes to
``market_us.fact_equity``. Auto-seeds missing instruments into ``ref.instruments``
on first run by querying Schwab's quote-reference metadata.

Usage:
    # 20-year daily for the S&P 500 (one-shot)
    python scripts/us/equities/schwab/us_equities_schwab_historical.py --universe sp500 --frequency 1d --from-date 2005-01-01

    # 1-minute, last 45 days (Schwab's max lookback for 1m)
    python scripts/us/equities/schwab/us_equities_schwab_historical.py --universe sp500 --frequency 1m

    # 5-minute, last 8 months
    python scripts/us/equities/schwab/us_equities_schwab_historical.py --universe sp500 --frequency 5m

    # Test with first 10 symbols only, no DB writes
    python scripts/us/equities/schwab/us_equities_schwab_historical.py --universe sp500 --frequency 1d --limit 10 --dry-run

Frequencies:
    1m / 5m / 10m / 15m / 30m / 1d
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import yaml
from sqlalchemy import text

# Path layout: scripts/us/equities/schwab/<file>.py
#   parents[0]=schwab, [1]=equities, [2]=us, [3]=scripts, [4]=repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]
assert (PROJECT_ROOT / "pyproject.toml").exists(), (
    f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"
)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.countries.us.equities.schwab import (  # noqa: E402
    fetch_daily_bars,
    fetch_intraday,
    fetch_quotes,
    get_client,
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
        logging.FileHandler(LOG_DIR / f"us_equities_schwab_historical_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("us_equities_schwab_historical")

# Schwab `reference.exchange` 1-letter codes -> our ref.exchanges.code
SCHWAB_EXCHANGE_MAP = {
    "Q": "NASDAQ",
    "N": "NYSE",
    "A": "NYSE",   # NYSE American (treat as NYSE for now)
    "P": "NYSE",   # NYSE Arca
}

# Schwab assetMainType -> (instrument_type, asset_class)
ASSET_TYPE_MAP = {
    "EQUITY": ("EQ", "equity"),
    "ETF": ("ETF", "etf"),
    "INDEX": ("INDEX", "index"),
    "MUTUAL_FUND": ("EQ", "equity"),
}


# ── Universe loading ────────────────────────────────────────────────────────


def load_universe(name: str) -> list[str]:
    """Load a universe spec.

    *name* may be a short name like ``sp500`` (resolved to ``configs/universes/us_sp500.yaml``)
    or an explicit path to a yaml file.
    """
    if name.endswith(".yaml") or "/" in name or "\\" in name:
        path = Path(name)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    else:
        path = UNIVERSES_DIR / f"us_{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")
    with open(path) as f:
        spec = yaml.safe_load(f)
    syms = spec.get("symbols") or []
    if not syms:
        raise ValueError(f"Universe {path} has no symbols")
    return syms


# ── Auto-seed missing instruments ───────────────────────────────────────────


def seed_missing_instruments(client, engine, symbols: list[str]) -> dict[str, UUID]:
    """Look up *symbols* in ref.instruments; auto-seed missing via Schwab metadata.

    Returns ``{trading_symbol: instrument_id}`` for ALL inputs.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT trading_symbol, id FROM ref.instruments
            WHERE market_code = 'USA' AND trading_symbol = ANY(:syms)
        """), {"syms": symbols}).fetchall()
    lookup = {r[0]: r[1] for r in rows}
    missing = [s for s in symbols if s not in lookup]
    if not missing:
        return lookup

    log.info("Auto-seeding %d missing US instruments via Schwab metadata", len(missing))

    # Cache exchange ids
    with engine.connect() as conn:
        ex_ids = dict(conn.execute(text(
            "SELECT code, id FROM ref.exchanges WHERE country_code = 'US'"
        )).fetchall())

    today = date.today()
    inserted = 0

    # Batch in chunks of 50 (Schwab get_quotes limit)
    for i in range(0, len(missing), 50):
        chunk = missing[i:i + 50]
        try:
            bundle = fetch_quotes(client, chunk)
        except Exception as e:
            log.error("Schwab quote-batch failed for %s: %s", chunk[:3], e)
            continue

        if bundle.invalid_symbols:
            log.warning("Schwab couldn't resolve: %s", bundle.invalid_symbols)

        ref_df = bundle.reference
        quote_df = bundle.quotes

        with engine.begin() as conn:
            for _, ref in ref_df.iterrows():
                sym = ref["symbol"]
                schwab_exch = ref.get("exchange") or "Q"
                ref_exch_code = SCHWAB_EXCHANGE_MAP.get(schwab_exch, "NASDAQ")
                exch_id = ex_ids.get(ref_exch_code)
                if exch_id is None:
                    log.error("No exchange_id for %s -> %s", sym, ref_exch_code)
                    continue

                qrow = quote_df[quote_df["symbol"] == sym]
                asset_main = (qrow.iloc[0].get("asset_main_type") if len(qrow) else None) or "EQUITY"
                inst_type, asset_class = ASSET_TYPE_MAP.get(asset_main, ("EQ", "equity"))

                row = conn.execute(text("""
                    INSERT INTO ref.instruments (
                        exchange_id, instrument_key, trading_symbol, name, isin,
                        segment, instrument_type, asset_class, market_code,
                        lot_size, status, first_seen, last_seen
                    ) VALUES (
                        :exch_id, :ikey, :sym, :name, NULL,
                        :seg, :itype, :acls, 'USA',
                        1, 'active', :today, :today
                    )
                    ON CONFLICT (instrument_key) DO UPDATE SET
                        last_seen = :today, updated_at = now()
                    RETURNING id, trading_symbol
                """), {
                    "exch_id": exch_id,
                    "ikey": f"{sym}.US",
                    "sym": sym,
                    "name": ref.get("description", sym),
                    "seg": "US_EQ",
                    "itype": inst_type,
                    "acls": asset_class,
                    "today": today,
                }).fetchone()
                if row:
                    lookup[row[1]] = row[0]
                    inserted += 1

        time.sleep(0.5)  # be gentle on the rate limiter

    log.info("Auto-seed complete: %d new instruments inserted", inserted)
    return lookup


# ── Per-symbol fetch ────────────────────────────────────────────────────────


def fetch_one(client, symbol: str, frequency: str,
              from_dt: datetime | None, to_dt: datetime | None):
    """Pull bars for one symbol at *frequency*. Returns DataFrame."""
    if frequency == "1d":
        return fetch_daily_bars(client, symbol, from_date=from_dt, to_date=to_dt)
    return fetch_intraday(
        client, symbol, frequency=frequency,
        start_datetime=from_dt, end_datetime=to_dt,
    )


# ── Main ────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Schwab US historical backfill")
    p.add_argument("--universe", required=True,
                   help="Universe short name (sp500 -> us_sp500.yaml) or yaml path")
    p.add_argument("--frequency", required=True,
                   choices=["1m", "5m", "10m", "15m", "30m", "1d"])
    p.add_argument("--from-date", help="YYYY-MM-DD (default: API max lookback)")
    p.add_argument("--to-date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--workers", type=int, default=4,
                   help="Concurrent symbol fetches (default 4)")
    p.add_argument("--limit", type=int, help="Cap symbols (testing)")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write to DB; just fetch and report")
    return p.parse_args()


def _coerce_date(d: str | None) -> datetime | None:
    if d is None:
        return None
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)


def main() -> int:
    args = parse_args()

    log.info("=" * 60)
    log.info("Schwab US historical backfill -- universe=%s freq=%s",
             args.universe, args.frequency)
    log.info("=" * 60)

    symbols = load_universe(args.universe)
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

    from_dt = _coerce_date(args.from_date)
    to_dt = _coerce_date(args.to_date)

    log.info("Fetching %s bars for %d symbols (workers=%d) ...",
             args.frequency, len(resolved), args.workers)

    total_bars = 0
    total_inserted = 0
    failures: list[tuple[str, str]] = []
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(fetch_one, client, sym, args.frequency, from_dt, to_dt): sym
            for sym in resolved
        }
        for i, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                df = fut.result()
                if df is None or df.empty:
                    if i % 50 == 0 or i == len(resolved):
                        log.info("[%d/%d] %s: empty", i, len(resolved), sym)
                    continue

                total_bars += len(df)
                if args.dry_run:
                    if i % 50 == 0 or i == len(resolved):
                        log.info("[%d/%d] %s: %d bars (dry-run)", i, len(resolved), sym, len(df))
                    continue

                n = write_candles(
                    df, inst_lookup[sym], None,
                    source="schwab_pricehistory",
                    engine=engine,
                    frequency=args.frequency,
                )
                total_inserted += n
                if i % 50 == 0 or i == len(resolved):
                    log.info("[%d/%d] %s: %d bars -> %d inserted",
                             i, len(resolved), sym, len(df), n)
            except Exception as e:
                log.error("[%d/%d] %s: %s", i, len(resolved), sym, str(e)[:200])
                failures.append((sym, str(e)[:200]))

    elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info("Done in %.1f s -- %d symbols, %d bars fetched, %d rows inserted, %d failures",
             elapsed, len(resolved), total_bars, total_inserted, len(failures))
    if failures:
        log.warning("First 10 failures:")
        for sym, err in failures[:10]:
            log.warning("  %s -> %s", sym, err)
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
