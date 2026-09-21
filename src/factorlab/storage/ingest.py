"""Legacy Postgres-first ingestion helpers.

These functions reflect the normalized SQLAlchemy storage path being replaced by
the ClickHouse architecture in docs/architecture/02-database-clickhouse.md.
"""

import logging
from datetime import date, datetime, timezone
from uuid import UUID

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


# Instrument types routed to {schema}.fact_equity. BE = NSE Book Entry segment
# (new listings post-demerger sit here for ~6-12 months before promotion to EQ);
# they're tradeable equities and use the same schema as EQ/ETF.
_EQUITY_TYPES = ("EQ", "ETF", "BE")


# ── Instrument sync ──────────────────────────────────────────────────────────


def sync_instruments(
    instruments: list[dict],
    engine: Engine,
    *,
    segment: str = "NSE_EQ",
    exchange_code: str = "NSE",
    market_code: str = "IND",
) -> dict[str, UUID]:
    """Upsert EQ instruments into ref.instruments.

    Returns ``{trading_symbol: instrument_id}`` for the synced records.
    """
    # EQ = regular rolling segment; BE = Book Entry / surveillance for new
    # listings (e.g. demerger spin-offs in nifty500). Both are tradeable.
    eq_records = [
        i for i in instruments
        if i.get("segment") == segment and i.get("instrument_type") in ("EQ", "BE")
    ]
    if not eq_records:
        log.warning("No EQ/BE instruments found for segment=%s", segment)
        return {}

    today = date.today()

    with engine.begin() as conn:
        exchange_id = conn.execute(
            text("SELECT id FROM ref.exchanges WHERE code = :code"),
            {"code": exchange_code},
        ).scalar()
        if not exchange_id:
            raise ValueError(f"Exchange '{exchange_code}' not found in ref.exchanges")

        # country_code + currency_code were dropped in mig 026 — derive via
        # JOIN to ref.markets using market_code.
        upsert_sql = text("""
            INSERT INTO ref.instruments (
                exchange_id, instrument_key, trading_symbol, name, isin,
                segment, instrument_type, asset_class, market_code,
                lot_size, tick_size, freeze_quantity, exchange_token,
                security_type, status, first_seen, last_seen
            ) VALUES (
                :exchange_id, :instrument_key, :trading_symbol, :name, :isin,
                :segment, :instrument_type, :asset_class, :market_code,
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
                "market_code": market_code,
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


def write_candles(
    df: pd.DataFrame,
    instrument_id: UUID,
    contract_id: UUID | None,
    source: str,
    engine: Engine,
    *,
    frequency: str = "1m",
) -> int:
    """Insert candles into the appropriate split fact table based on asset class.

    Routing:
        contract_id NOT NULL                                  -> fact_futures
        contract_id NULL AND instrument_type IN ('EQ','ETF','BE')  -> fact_equity
        contract_id NULL AND instrument_type = 'INDEX'        -> fact_index

    Schema dispatch by instrument's country_code:
        IN  -> market_in.fact_*
        US  -> market_us.fact_*

    Expects DataFrame with columns: open, high, low, close, volume.
    Timestamp column may be named ``bar_time`` (Schwab pattern) OR ``timestamp``
    (Upstox pattern) — first one found wins.
    For futures rows, also expects ``oi``.
    For US intraday rows, may include a ``session`` column ('pre'/'regular'/'post');
    if absent the table's server default ('regular') applies.

    Args:
        frequency: Bar frequency code matching ``ref.frequencies.code``
                   ('1m', '5m', '15m', '1h', '1d', etc.). Default '1m'.
        source: Maps to ``ref.data_endpoints.code`` — must resolve, else raises.

    Returns count of rows actually inserted (after dedup-on-conflict).
    """
    if df is None or df.empty:
        return 0

    # Pick the timestamp column name (bar_time preferred, fallback to timestamp)
    if "bar_time" in df.columns:
        ts_col = "bar_time"
    elif "timestamp" in df.columns:
        ts_col = "timestamp"
    else:
        raise ValueError(
            "DataFrame must have a 'bar_time' or 'timestamp' column"
        )
    has_session = "session" in df.columns
    has_adj_close = "adj_close" in df.columns

    inst_str = str(instrument_id)
    contract_str = str(contract_id) if contract_id else None
    inserted = 0

    with engine.begin() as conn:
        # Resolve dispatch keys ONCE per call (instrument doesn't change per call)
        meta = conn.execute(text("""
            SELECT i.instrument_type, m.country_code
            FROM ref.instruments i
            JOIN ref.markets m ON m.code = i.market_code
            WHERE i.id = :iid
        """), {"iid": inst_str}).fetchone()
        if meta is None:
            raise ValueError(f"unknown instrument_id={inst_str}")
        instrument_type, country_code = meta

        if country_code == "IN":
            mkt_schema = "market_in"
        elif country_code == "US":
            mkt_schema = "market_us"
        else:
            raise ValueError(
                f"unsupported country_code={country_code!r} for write_candles "
                f"(only IN, US wired today)"
            )

        # Resolve freq + endpoint
        freq_id = conn.execute(text(
            "SELECT id FROM ref.frequencies WHERE code = :code"
        ), {"code": frequency}).scalar()
        if freq_id is None:
            raise ValueError(
                f"frequency={frequency!r} does not match any ref.frequencies.code"
            )
        endpoint_id = conn.execute(text(
            "SELECT id FROM ref.data_endpoints WHERE code = :c"
        ), {"c": source}).scalar()
        if endpoint_id is None:
            raise ValueError(
                f"source={source!r} does not match any ref.data_endpoints.code"
            )

        # Build the per-class INSERT, conditionally including session + adj_close
        sess_col = ", session" if has_session else ""
        sess_val = ", :session" if has_session else ""
        # adj_close only on fact_equity (fact_index doesn't have it; fact_futures doesn't either)
        adj_col = ", adj_close" if has_adj_close else ""
        adj_val = ", :adj_close" if has_adj_close else ""

        if contract_str:
            # fact_futures (no adj_close column)
            insert_sql = text(f"""
                INSERT INTO {mkt_schema}.fact_futures
                    (contract_id, instrument_id, freq_id, bar_time,
                     open, high, low, close, volume, oi, endpoint_id{sess_col})
                VALUES
                    (:cid, :iid, :fid, :bar_time,
                     :open, :high, :low, :close, :volume, :oi, :eid{sess_val})
                ON CONFLICT DO NOTHING
            """)
        elif instrument_type in _EQUITY_TYPES:
            insert_sql = text(f"""
                INSERT INTO {mkt_schema}.fact_equity
                    (instrument_id, freq_id, bar_time,
                     open, high, low, close, volume, endpoint_id{sess_col}{adj_col})
                VALUES
                    (:iid, :fid, :bar_time,
                     :open, :high, :low, :close, :volume, :eid{sess_val}{adj_val})
                ON CONFLICT DO NOTHING
            """)
        elif instrument_type == "INDEX":
            # fact_index has no adj_close column
            insert_sql = text(f"""
                INSERT INTO {mkt_schema}.fact_index
                    (instrument_id, freq_id, bar_time,
                     open, high, low, close, volume, endpoint_id{sess_col})
                VALUES
                    (:iid, :fid, :bar_time,
                     :open, :high, :low, :close, :volume, :eid{sess_val})
                ON CONFLICT DO NOTHING
            """)
        else:
            raise ValueError(
                f"unhandled instrument_type={instrument_type!r} for "
                f"instrument_id={inst_str} (no contract_id)"
            )

        # Batch all rows into one executemany call (huge speedup for >1K rows)
        wants_adj = has_adj_close and instrument_type in _EQUITY_TYPES and not contract_str
        params_list: list[dict] = []
        for _, row in df.iterrows():
            p = {
                "iid": inst_str,
                "fid": freq_id,
                "eid": endpoint_id,
                "bar_time": row[ts_col],
                "open": float(row["open"]) if pd.notna(row["open"]) else None,
                "high": float(row["high"]) if pd.notna(row["high"]) else None,
                "low": float(row["low"]) if pd.notna(row["low"]) else None,
                "close": float(row["close"]) if pd.notna(row["close"]) else None,
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
            }
            if has_session:
                p["session"] = row["session"]
            if wants_adj:
                p["adj_close"] = (
                    float(row["adj_close"]) if pd.notna(row["adj_close"]) else None
                )
            if contract_str:
                p["cid"] = contract_str
                p["oi"] = int(row["oi"]) if pd.notna(row.get("oi")) else 0
            params_list.append(p)

        # executemany — one round-trip for the whole batch
        if params_list:
            result = conn.execute(insert_sql, params_list)
            # rowcount on executemany = total rows that affected the table
            # (with ON CONFLICT DO NOTHING, that's rows actually inserted)
            inserted = result.rowcount if result.rowcount and result.rowcount > 0 else 0

    return inserted


def write_candles_daily(
    df: pd.DataFrame,
    instrument_id: UUID,
    source: str,
    engine: Engine,
    *,
    contract_id: UUID | None = None,
) -> int:
    """Insert daily bars into the appropriate split fact table.

    Same routing as `write_candles` (asset class + country dispatch). Daily bars
    use freq_id='1d'. `trade_date` becomes `bar_time` at midnight UTC.
    `adj_close` is preserved (only meaningful on fact_equity).

    Expects DataFrame with columns: trade_date, open, high, low, close, adj_close, volume.
    Returns count of rows actually inserted.
    """
    if df is None or df.empty:
        return 0

    inst_str = str(instrument_id)
    contract_str = str(contract_id) if contract_id else None
    inserted = 0

    with engine.begin() as conn:
        meta = conn.execute(text("""
            SELECT i.instrument_type, m.country_code
            FROM ref.instruments i
            JOIN ref.markets m ON m.code = i.market_code
            WHERE i.id = :iid
        """), {"iid": inst_str}).fetchone()
        if meta is None:
            raise ValueError(f"unknown instrument_id={inst_str}")
        instrument_type, country_code = meta
        mkt_schema = "market_in" if country_code == "IN" else (
            "market_us" if country_code == "US" else None
        )
        if mkt_schema is None:
            raise ValueError(f"unsupported country_code={country_code!r}")

        freq_id = conn.execute(text(
            "SELECT id FROM ref.frequencies WHERE code = '1d'"
        )).scalar()
        endpoint_id = conn.execute(text(
            "SELECT id FROM ref.data_endpoints WHERE code = :c"
        ), {"c": source}).scalar()
        if endpoint_id is None:
            raise ValueError(
                f"source={source!r} does not match any ref.data_endpoints.code"
            )

        if contract_str:
            insert_sql = text(f"""
                INSERT INTO {mkt_schema}.fact_futures
                    (contract_id, instrument_id, freq_id, bar_time,
                     open, high, low, close, volume, oi, endpoint_id)
                VALUES
                    (:cid, :iid, :fid, :bar_time,
                     :open, :high, :low, :close, :volume, 0, :eid)
                ON CONFLICT DO NOTHING
            """)
        elif instrument_type in _EQUITY_TYPES:
            insert_sql = text(f"""
                INSERT INTO {mkt_schema}.fact_equity
                    (instrument_id, freq_id, bar_time,
                     open, high, low, close, adj_close, volume, endpoint_id)
                VALUES
                    (:iid, :fid, :bar_time,
                     :open, :high, :low, :close, :adj_close, :volume, :eid)
                ON CONFLICT DO NOTHING
            """)
        elif instrument_type == "INDEX":
            insert_sql = text(f"""
                INSERT INTO {mkt_schema}.fact_index
                    (instrument_id, freq_id, bar_time,
                     open, high, low, close, volume, endpoint_id)
                VALUES
                    (:iid, :fid, :bar_time,
                     :open, :high, :low, :close, :volume, :eid)
                ON CONFLICT DO NOTHING
            """)
        else:
            raise ValueError(
                f"unhandled instrument_type={instrument_type!r}"
            )

        for _, row in df.iterrows():
            params = {
                "iid": inst_str,
                "fid": freq_id,
                "eid": endpoint_id,
                "bar_time": pd.to_datetime(row["trade_date"]).to_pydatetime(),
                "open": float(row["open"]) if pd.notna(row["open"]) else None,
                "high": float(row["high"]) if pd.notna(row["high"]) else None,
                "low": float(row["low"]) if pd.notna(row["low"]) else None,
                "close": float(row["close"]) if pd.notna(row["close"]) else None,
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
            }
            if contract_str:
                params["cid"] = contract_str
            elif instrument_type in _EQUITY_TYPES:
                params["adj_close"] = (
                    float(row["adj_close"]) if pd.notna(row.get("adj_close")) else None
                )
            result = conn.execute(insert_sql, params)
            inserted += result.rowcount

    return inserted
