"""DB-first ingestion: sync instruments/contracts, write candles.

All writes go to Postgres (canonical). Callers save Arrow IPC as secondary cache.
"""

import logging
from datetime import date, datetime, timezone
from uuid import UUID

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


# ── Instrument sync ──────────────────────────────────────────────────────────


def sync_instruments(
    instruments: list[dict],
    engine: Engine,
    *,
    segment: str = "NSE_EQ",
    exchange_code: str = "NSE",
    country_code: str = "IN",
    market_code: str = "IND",
    currency_code: str = "INR",
) -> dict[str, UUID]:
    """Upsert EQ instruments into ref.instruments.

    Returns ``{trading_symbol: instrument_id}`` for the synced records.
    """
    eq_records = [
        i for i in instruments
        if i.get("segment") == segment and i.get("instrument_type") == "EQ"
    ]
    if not eq_records:
        log.warning("No EQ instruments found for segment=%s", segment)
        return {}

    today = date.today()

    with engine.begin() as conn:
        # Lookup exchange_id once
        exchange_id = conn.execute(
            text("SELECT id FROM ref.exchanges WHERE code = :code"),
            {"code": exchange_code},
        ).scalar()
        if not exchange_id:
            raise ValueError(f"Exchange '{exchange_code}' not found in ref.exchanges")

        upsert_sql = text("""
            INSERT INTO ref.instruments (
                exchange_id, instrument_key, trading_symbol, name, isin,
                segment, instrument_type, asset_class,
                country_code, market_code, currency_code,
                lot_size, tick_size, freeze_quantity, exchange_token,
                security_type, status, first_seen, last_seen
            ) VALUES (
                :exchange_id, :instrument_key, :trading_symbol, :name, :isin,
                :segment, :instrument_type, :asset_class,
                :country_code, :market_code, :currency_code,
                :lot_size, :tick_size, :freeze_quantity, :exchange_token,
                :security_type, 'active', :today, :today
            )
            ON CONFLICT (instrument_key) DO UPDATE SET
                last_seen = :today,
                lot_size = EXCLUDED.lot_size,
                tick_size = EXCLUDED.tick_size,
                freeze_quantity = EXCLUDED.freeze_quantity,
                updated_at = now()
            RETURNING id, trading_symbol
        """)

        lookup: dict[str, UUID] = {}
        for rec in eq_records:
            itype = rec.get("instrument_type", "EQ")
            asset_class = {"EQ": "equity", "INDEX": "index", "ETF": "etf"}.get(itype, "equity")

            row = conn.execute(upsert_sql, {
                "exchange_id": exchange_id,
                "instrument_key": rec["instrument_key"],
                "trading_symbol": rec["trading_symbol"],
                "name": rec.get("name", rec["trading_symbol"]),
                "isin": rec.get("isin"),
                "segment": rec.get("segment", segment),
                "instrument_type": itype,
                "asset_class": asset_class,
                "country_code": country_code,
                "market_code": market_code,
                "currency_code": currency_code,
                "lot_size": rec.get("lot_size", 1),
                "tick_size": rec.get("tick_size"),
                "freeze_quantity": rec.get("freeze_quantity"),
                "exchange_token": str(rec.get("exchange_token", "")),
                "security_type": rec.get("security_type"),
                "today": today,
            }).fetchone()

            if row:
                lookup[row[1]] = row[0]

    log.info("Synced %d instruments to ref.instruments", len(lookup))
    return lookup


# ── Contract sync ────────────────────────────────────────────────────────────


def sync_contracts(
    instruments: list[dict],
    instrument_lookup: dict[str, UUID],
    engine: Engine,
    *,
    segment: str = "NSE_FO",
    exchange_code: str = "NSE",
) -> dict[str, UUID]:
    """Upsert FUT contracts into ref.contracts.

    Only syncs contracts whose underlying is in *instrument_lookup*.
    Returns ``{contract_key: contract_id}``.
    """
    fut_records = [
        i for i in instruments
        if i.get("segment") == segment and i.get("instrument_type") == "FUT"
        and i.get("underlying_symbol") in instrument_lookup
    ]
    if not fut_records:
        log.info("No FUT contracts to sync (0 matching underlyings)")
        return {}

    today = date.today()

    with engine.begin() as conn:
        exchange_id = conn.execute(
            text("SELECT id FROM ref.exchanges WHERE code = :code"),
            {"code": exchange_code},
        ).scalar()

        upsert_sql = text("""
            INSERT INTO ref.contracts (
                instrument_id, exchange_id, contract_key, trading_symbol,
                contract_type, segment, expiry, strike_price,
                lot_size, tick_size, freeze_quantity, exchange_token,
                weekly, status, first_seen, last_seen
            ) VALUES (
                :instrument_id, :exchange_id, :contract_key, :trading_symbol,
                :contract_type, :segment, :expiry, :strike_price,
                :lot_size, :tick_size, :freeze_quantity, :exchange_token,
                :weekly, 'active', :today, :today
            )
            ON CONFLICT (contract_key) DO UPDATE SET
                last_seen = :today,
                lot_size = EXCLUDED.lot_size,
                tick_size = EXCLUDED.tick_size,
                freeze_quantity = EXCLUDED.freeze_quantity,
                status = 'active',
                updated_at = now()
            RETURNING id, contract_key
        """)

        lookup: dict[str, UUID] = {}
        for rec in fut_records:
            underlying = rec.get("underlying_symbol")
            inst_id = instrument_lookup.get(underlying)
            if not inst_id:
                continue

            # Convert epoch ms → date
            expiry_ms = rec.get("expiry", 0)
            expiry_date = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).date()

            row = conn.execute(upsert_sql, {
                "instrument_id": str(inst_id),
                "exchange_id": exchange_id,
                "contract_key": rec["instrument_key"],
                "trading_symbol": rec.get("trading_symbol", ""),
                "contract_type": rec.get("instrument_type", "FUT"),
                "segment": rec.get("segment", segment),
                "expiry": expiry_date,
                "strike_price": rec.get("strike_price", 0),
                "lot_size": rec.get("lot_size", 1),
                "tick_size": rec.get("tick_size"),
                "freeze_quantity": rec.get("freeze_quantity"),
                "exchange_token": str(rec.get("exchange_token", "")),
                "weekly": rec.get("weekly", False),
                "today": today,
            }).fetchone()

            if row:
                lookup[row[1]] = row[0]

    log.info("Synced %d contracts to ref.contracts", len(lookup))
    return lookup


# ── Candle writer ────────────────────────────────────────────────────────────


def write_candles(
    df: pd.DataFrame,
    instrument_id: UUID,
    contract_id: UUID | None,
    source: str,
    engine: Engine,
) -> int:
    """Insert candles into market.candles_1min. Dedup via partial unique indexes.

    Expects DataFrame with columns: timestamp, open, high, low, close, volume, oi.
    Returns count of rows actually inserted.
    """
    if df is None or df.empty:
        return 0

    insert_sql = text("""
        INSERT INTO market.candles_1min
            (instrument_id, contract_id, bar_time, open, high, low, close, volume, oi, source)
        VALUES
            (:inst_id, :contract_id, :bar_time, :open, :high, :low, :close, :volume, :oi, :source)
        ON CONFLICT DO NOTHING
    """)

    inst_str = str(instrument_id)
    contract_str = str(contract_id) if contract_id else None
    inserted = 0

    with engine.begin() as conn:
        for _, row in df.iterrows():
            result = conn.execute(insert_sql, {
                "inst_id": inst_str,
                "contract_id": contract_str,
                "bar_time": row["timestamp"],
                "open": float(row["open"]) if pd.notna(row["open"]) else None,
                "high": float(row["high"]) if pd.notna(row["high"]) else None,
                "low": float(row["low"]) if pd.notna(row["low"]) else None,
                "close": float(row["close"]) if pd.notna(row["close"]) else None,
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
                "oi": int(row["oi"]) if pd.notna(row["oi"]) else 0,
                "source": source,
            })
            inserted += result.rowcount

    return inserted


# ── Daily candle writer ─────────────────────────────────────────────────────


def write_candles_daily(
    df: pd.DataFrame,
    instrument_id: UUID,
    source: str,
    engine: Engine,
    *,
    contract_id: UUID | None = None,
) -> int:
    """Insert daily bars into market.candles_daily. Dedup via partial unique indexes.

    Expects DataFrame with columns: trade_date, open, high, low, close, adj_close, volume.
    Returns count of rows actually inserted.
    """
    if df is None or df.empty:
        return 0

    insert_sql = text("""
        INSERT INTO market.candles_daily
            (instrument_id, contract_id, trade_date, open, high, low, close, adj_close, volume, source)
        VALUES
            (:inst_id, :contract_id, :trade_date, :open, :high, :low, :close, :adj_close, :volume, :source)
        ON CONFLICT DO NOTHING
    """)

    inst_str = str(instrument_id)
    contract_str = str(contract_id) if contract_id else None
    inserted = 0

    with engine.begin() as conn:
        for _, row in df.iterrows():
            result = conn.execute(insert_sql, {
                "inst_id": inst_str,
                "contract_id": contract_str,
                "trade_date": row["trade_date"],
                "open": float(row["open"]) if pd.notna(row["open"]) else None,
                "high": float(row["high"]) if pd.notna(row["high"]) else None,
                "low": float(row["low"]) if pd.notna(row["low"]) else None,
                "close": float(row["close"]) if pd.notna(row["close"]) else None,
                "adj_close": float(row["adj_close"]) if pd.notna(row.get("adj_close")) else None,
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
                "source": source,
            })
            inserted += result.rowcount

    return inserted
