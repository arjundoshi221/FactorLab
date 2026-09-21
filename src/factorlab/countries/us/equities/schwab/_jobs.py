"""Shared helpers for Schwab data jobs (historical, live cron, EOD cron).

Three reusable pieces:
  - ``load_universe(path)`` -- read a configs/universes/us_*.yaml
  - ``seed_missing_instruments(client, engine, symbols)`` -- auto-insert into ref.instruments
  - ``fetch_one(client, symbol, frequency, ...)`` -- dispatch to daily/intraday fetcher

Used by:
  scripts/us/equities/schwab/us_equities_schwab_historical.py
  scripts/us/equities/schwab/us_equities_schwab_live.py
  scripts/us/equities/schwab/us_equities_schwab_eod.py
"""

import logging
import time
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import yaml
from sqlalchemy import text

from factorlab.countries.us.equities.schwab.candles import fetch_daily_bars
from factorlab.countries.us.equities.schwab.intraday import fetch_intraday
from factorlab.countries.us.equities.schwab.quotes import fetch_quotes

log = logging.getLogger(__name__)


# Schwab `reference.exchange` 1-letter codes -> our ref.exchanges.code
SCHWAB_EXCHANGE_MAP = {
    "Q": "NASDAQ",
    "N": "NYSE",
    "A": "NYSE",   # NYSE American
    "P": "NYSE",   # NYSE Arca
}

# Schwab assetMainType -> (instrument_type, asset_class)
ASSET_TYPE_MAP = {
    "EQUITY": ("EQ", "equity"),
    "ETF": ("ETF", "etf"),
    "INDEX": ("INDEX", "index"),
    "MUTUAL_FUND": ("EQ", "equity"),
}


def load_universe(yaml_path: Path) -> list[str]:
    """Read symbols list from a universe YAML."""
    if not yaml_path.exists():
        raise FileNotFoundError(f"Universe file not found: {yaml_path}")
    with open(yaml_path) as f:
        spec = yaml.safe_load(f)
    syms = spec.get("symbols") or []
    if not syms:
        raise ValueError(f"Universe {yaml_path} has no symbols")
    return syms


def seed_missing_instruments(client, engine, symbols: list[str]) -> dict[str, UUID]:
    """Look up *symbols* in ref.instruments; auto-seed missing via Schwab metadata.

    Returns ``{trading_symbol: instrument_id}`` for ALL resolvable inputs.
    Symbols Schwab can't resolve are omitted from the returned dict.
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

        time.sleep(0.5)  # gentle on Schwab's rate limiter

    log.info("Auto-seed complete: %d new instruments inserted", inserted)
    return lookup


def fetch_one(
    client,
    symbol: str,
    frequency: str,
    *,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    retries: int = 3,
):
    """Pull bars for one symbol at *frequency*. Returns DataFrame.

    Wraps the daily/intraday dispatch with exponential-backoff retry on
    transient errors (429, network blips). Schwab-py raises RuntimeError
    on non-200; we retry 3× with 2s, 4s, 8s sleeps before giving up.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if frequency == "1d":
                return fetch_daily_bars(client, symbol, from_date=from_dt, to_date=to_dt)
            return fetch_intraday(
                client, symbol, frequency=frequency,
                start_datetime=from_dt, end_datetime=to_dt,
            )
        except Exception as e:
            last_exc = e
            msg = str(e)
            # Only retry on transient signals — auth/symbol errors should fail fast
            msg_lc = msg.lower()
            transient = (
                "429" in msg or "500" in msg or "502" in msg or "503" in msg
                or "504" in msg
                or "timeout" in msg_lc or "timed out" in msg_lc
                or "connection" in msg_lc or "read operation" in msg_lc
            )
            if not transient or attempt == retries:
                raise
            wait = 2 ** (attempt + 1)
            log.warning("%s: transient error (attempt %d/%d), sleeping %ds — %s",
                        symbol, attempt + 1, retries, wait, msg[:120])
            time.sleep(wait)
    # unreachable, but keeps type-checkers happy
    raise last_exc  # type: ignore[misc]
