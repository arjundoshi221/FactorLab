"""Sync EODHD exchange symbol list → ref.instruments.

Maps EODHD's /exchange-symbol-list/US response into the canonical instrument schema.
"""

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from factorlab.sources.eodhd.client import EODHDClient

log = logging.getLogger(__name__)

# EODHD Type → our instrument_type + asset_class
TYPE_MAP = {
    "Common Stock": ("EQ", "equity"),
    "ETF": ("ETF", "etf"),
    "Preferred Stock": ("EQ", "equity"),
    "Fund": ("ETF", "etf"),
}


def fetch_us_instruments(client: EODHDClient) -> list[dict]:
    """Fetch US exchange symbol list and normalize to our instrument format.

    Filters to Common Stock + ETF only (skips warrants, bonds, etc.).
    """
    raw = client.get_exchange_symbols("US")
    instruments = []
    for item in raw:
        eodhd_type = item.get("Type", "")
        if eodhd_type not in TYPE_MAP:
            continue

        itype, asset_class = TYPE_MAP[eodhd_type]
        ticker = item["Code"]
        instruments.append({
            "instrument_key": f"{ticker}.US",
            "trading_symbol": ticker,
            "name": item.get("Name", ticker),
            "isin": item.get("Isin") or None,
            "segment": "US_EQ",
            "instrument_type": itype,
            "asset_class": asset_class,
        })

    log.info("Parsed %d US instruments (Common Stock + ETF) from %d raw", len(instruments), len(raw))
    return instruments


def sync_us_instruments(
    instruments: list[dict],
    engine: Engine,
    *,
    exchange_code: str = "NYSE",
    country_code: str = "US",
    market_code: str = "USA",
    currency_code: str = "USD",
) -> dict[str, UUID]:
    """Upsert US instruments into ref.instruments.

    Returns {instrument_key: instrument_id} for synced records.
    """
    if not instruments:
        return {}

    today = date.today()

    with engine.begin() as conn:
        exchange_id = conn.execute(
            text("SELECT id FROM ref.exchanges WHERE code = :code"),
            {"code": exchange_code},
        ).scalar()
        if not exchange_id:
            raise ValueError(f"Exchange '{exchange_code}' not in ref.exchanges — run seed migration first")

        upsert_sql = text("""
            INSERT INTO ref.instruments (
                exchange_id, instrument_key, trading_symbol, name, isin,
                segment, instrument_type, asset_class,
                country_code, market_code, currency_code,
                lot_size, status, first_seen, last_seen
            ) VALUES (
                :exchange_id, :instrument_key, :trading_symbol, :name, :isin,
                :segment, :instrument_type, :asset_class,
                :country_code, :market_code, :currency_code,
                1, 'active', :today, :today
            )
            ON CONFLICT (instrument_key) DO UPDATE SET
                name = EXCLUDED.name,
                isin = COALESCE(EXCLUDED.isin, ref.instruments.isin),
                last_seen = :today,
                updated_at = now()
            RETURNING id, instrument_key
        """)

        lookup: dict[str, UUID] = {}
        for rec in instruments:
            row = conn.execute(upsert_sql, {
                "exchange_id": exchange_id,
                "instrument_key": rec["instrument_key"],
                "trading_symbol": rec["trading_symbol"],
                "name": rec["name"],
                "isin": rec.get("isin"),
                "segment": rec["segment"],
                "instrument_type": rec["instrument_type"],
                "asset_class": rec["asset_class"],
                "country_code": country_code,
                "market_code": market_code,
                "currency_code": currency_code,
                "today": today,
            }).fetchone()
            if row:
                lookup[row[1]] = row[0]

    log.info("Synced %d US instruments to ref.instruments", len(lookup))
    return lookup
