"""batch_test_db — End-to-end verification of the FactorLab database schema.

Verifies:
  1. All ref seed data exists (countries, currencies, fx_pairs, markets, exchanges)
  2. Insert a test instrument (RELIANCE EQ) and contract (RELIANCE FUT)
  3. Insert test candles (5 rows of 1-min data for EQ and FUT)
  4. Query candles joined through instruments → exchanges → markets → countries
  5. Verify TimescaleDB hypertable status
  6. Clean up all test data

Usage:
  python scripts/batch_test_db.py
  python scripts/batch_test_db.py --keep   # skip cleanup
"""

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.storage.db import get_engine  # noqa: E402


def run_tests(keep: bool = False) -> int:
    engine = get_engine()
    errors = 0
    test_instrument_id = None
    test_contract_id = None

    with engine.connect() as conn:
        print("=" * 60)
        print("FactorLab Database Schema — Batch Test")
        print("=" * 60)

        # ── 1. Verify seed data ──────────────────────────────────────
        print("\n[1] Verifying seed data...")

        countries = conn.execute(text("SELECT code, name FROM ref.countries ORDER BY code")).fetchall()
        print(f"  ref.countries: {len(countries)} rows")
        expected_countries = {"DE", "GB", "IN", "SG", "US"}
        actual_countries = {r[0] for r in countries}
        if actual_countries != expected_countries:
            print(f"  ERROR: Expected {expected_countries}, got {actual_countries}")
            errors += 1
        else:
            print(f"  OK: {sorted(actual_countries)}")

        currencies = conn.execute(text("SELECT code FROM ref.currencies ORDER BY code")).fetchall()
        print(f"  ref.currencies: {len(currencies)} rows")
        expected_currencies = {"EUR", "GBP", "INR", "SGD", "USD"}
        actual_currencies = {r[0] for r in currencies}
        if actual_currencies != expected_currencies:
            print(f"  ERROR: Expected {expected_currencies}, got {actual_currencies}")
            errors += 1
        else:
            print(f"  OK: {sorted(actual_currencies)}")

        fx_count = conn.execute(text("SELECT count(*) FROM ref.fx_pairs")).scalar()
        print(f"  ref.fx_pairs: {fx_count} rows", "OK" if fx_count == 10 else f"ERROR: expected 10")
        if fx_count != 10:
            errors += 1

        markets = conn.execute(text("SELECT code FROM ref.markets ORDER BY code")).fetchall()
        print(f"  ref.markets: {len(markets)} rows")
        expected_markets = {"IND", "USA"}
        actual_markets = {r[0] for r in markets}
        if actual_markets != expected_markets:
            print(f"  ERROR: Expected {expected_markets}, got {actual_markets}")
            errors += 1
        else:
            print(f"  OK: {sorted(actual_markets)}")

        exchanges = conn.execute(text("SELECT code FROM ref.exchanges ORDER BY code")).fetchall()
        print(f"  ref.exchanges: {len(exchanges)} rows")
        expected_exchanges = {"BSE", "NASDAQ", "NSE", "NYSE"}
        actual_exchanges = {r[0] for r in exchanges}
        if actual_exchanges != expected_exchanges:
            print(f"  ERROR: Expected {expected_exchanges}, got {actual_exchanges}")
            errors += 1
        else:
            print(f"  OK: {sorted(actual_exchanges)}")

        # ── 2. Insert test instrument + contract ─────────────────────
        print("\n[2] Inserting test instrument + contract...")

        nse_id = conn.execute(text("SELECT id FROM ref.exchanges WHERE code = 'NSE'")).scalar()
        test_instrument_id = uuid4()

        conn.execute(text("""
            INSERT INTO ref.instruments
                (id, exchange_id, instrument_key, trading_symbol, name, isin, segment,
                 instrument_type, asset_class, country_code, market_code, currency_code,
                 lot_size, tick_size, status, first_seen)
            VALUES
                (:id, :exchange_id, 'NSE_EQ|INE002A01018', 'RELIANCE', 'Reliance Industries Ltd',
                 'INE002A01018', 'NSE_EQ', 'EQ', 'equity', 'IN', 'IND', 'INR',
                 1, 0.05, 'active', :today)
        """), {
            "id": str(test_instrument_id),
            "exchange_id": nse_id,
            "today": date.today(),
        })
        print(f"  Inserted instrument: RELIANCE (id={test_instrument_id})")

        test_contract_id = uuid4()
        conn.execute(text("""
            INSERT INTO ref.contracts
                (id, instrument_id, exchange_id, contract_key, trading_symbol,
                 contract_type, segment, expiry, strike_price, lot_size, status, first_seen)
            VALUES
                (:id, :instrument_id, :exchange_id, 'NSE_FO|99999', 'RELIANCE FUT 26 MAY 29',
                 'FUT', 'NSE_FO', '2026-05-29', 0, 250, 'active', :today)
        """), {
            "id": str(test_contract_id),
            "instrument_id": str(test_instrument_id),
            "exchange_id": nse_id,
            "today": date.today(),
        })
        print(f"  Inserted contract: RELIANCE FUT MAY 29 (id={test_contract_id})")

        # ── 3. Insert test candles ───────────────────────────────────
        print("\n[3] Inserting test candles...")

        now = datetime.now(timezone.utc)
        base_ts = now.replace(hour=4, minute=0, second=0, microsecond=0)  # ~09:30 IST

        # EQ candles (contract_id = NULL)
        for i in range(5):
            ts = base_ts.replace(minute=i)
            conn.execute(text("""
                INSERT INTO market.candles_1min
                    (instrument_id, contract_id, bar_time, open, high, low, close, volume, oi, source)
                VALUES
                    (:inst_id, NULL, :ts, :o, :h, :l, :c, :vol, 0, 'test')
            """), {
                "inst_id": str(test_instrument_id),
                "ts": ts,
                "o": 2800 + i, "h": 2810 + i, "l": 2795 + i, "c": 2805 + i,
                "vol": 10000 + i * 1000,
            })

        # FUT candles (contract_id set)
        for i in range(5):
            ts = base_ts.replace(minute=i)
            conn.execute(text("""
                INSERT INTO market.candles_1min
                    (instrument_id, contract_id, bar_time, open, high, low, close, volume, oi, source)
                VALUES
                    (:inst_id, :contract_id, :ts, :o, :h, :l, :c, :vol, :oi, 'test')
            """), {
                "inst_id": str(test_instrument_id),
                "contract_id": str(test_contract_id),
                "ts": ts,
                "o": 2810 + i, "h": 2820 + i, "l": 2805 + i, "c": 2815 + i,
                "vol": 5000 + i * 500,
                "oi": 120000,
            })

        eq_count = conn.execute(text("""
            SELECT count(*) FROM market.candles_1min
            WHERE instrument_id = :id AND contract_id IS NULL
        """), {"id": str(test_instrument_id)}).scalar()

        fut_count = conn.execute(text("""
            SELECT count(*) FROM market.candles_1min
            WHERE instrument_id = :id AND contract_id IS NOT NULL
        """), {"id": str(test_instrument_id)}).scalar()

        print(f"  EQ candles: {eq_count} rows (expected 5)")
        print(f"  FUT candles: {fut_count} rows (expected 5)")
        if eq_count != 5 or fut_count != 5:
            errors += 1

        # ── 4. Joined query: candles → instruments → exchanges → markets → countries ──
        print("\n[4] Testing joined query across schemas...")

        result = conn.execute(text("""
            SELECT
                c.bar_time,
                c.close,
                i.trading_symbol,
                e.code AS exchange,
                m.code AS market,
                co.name AS country
            FROM market.candles_1min c
            JOIN ref.instruments i ON c.instrument_id = i.id
            JOIN ref.exchanges e ON i.exchange_id = e.id
            JOIN ref.markets m ON e.market_code = m.code
            JOIN ref.countries co ON m.country_code = co.code
            WHERE c.instrument_id = :id AND c.contract_id IS NULL
            ORDER BY c.bar_time
            LIMIT 1
        """), {"id": str(test_instrument_id)}).fetchone()

        if result:
            print(f"  bar_time={result[0]}, close={result[1]}, symbol={result[2]}, "
                  f"exchange={result[3]}, market={result[4]}, country={result[5]}")
            if result[2] != "RELIANCE" or result[3] != "NSE" or result[4] != "IND" or result[5] != "India":
                print("  ERROR: Unexpected join values")
                errors += 1
            else:
                print("  OK: Full join path verified")
        else:
            print("  ERROR: No rows returned from join query")
            errors += 1

        # ── 5. Verify TimescaleDB hypertables ────────────────────────
        print("\n[5] Checking TimescaleDB hypertables...")

        hypertables = conn.execute(text("""
            SELECT hypertable_schema, hypertable_name
            FROM timescaledb_information.hypertables
            WHERE hypertable_schema = 'market'
            ORDER BY hypertable_name
        """)).fetchall()

        ht_names = {r[1] for r in hypertables}
        expected_ht = {"candles_1min", "candles_daily"}
        print(f"  Hypertables found: {sorted(ht_names)}")
        if ht_names != expected_ht:
            print(f"  ERROR: Expected {expected_ht}")
            errors += 1
        else:
            print("  OK: Both hypertables active")

        # ── 6. Cleanup ───────────────────────────────────────────────
        if keep:
            print("\n[6] Skipping cleanup (--keep flag)")
            conn.commit()
        else:
            print("\n[6] Cleaning up test data...")
            conn.execute(text("DELETE FROM market.candles_1min WHERE source = 'test'"))
            conn.execute(text("DELETE FROM ref.contracts WHERE id = :id"), {"id": str(test_contract_id)})
            conn.execute(text("DELETE FROM ref.instruments WHERE id = :id"), {"id": str(test_instrument_id)})
            conn.commit()
            print("  Cleaned up all test rows")

        # ── Summary ──────────────────────────────────────────────────
        print("\n" + "=" * 60)
        if errors == 0:
            print("ALL TESTS PASSED")
        else:
            print(f"FAILED: {errors} error(s)")
        print("=" * 60)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="FactorLab DB schema batch test")
    parser.add_argument("--keep", action="store_true", help="Keep test data (skip cleanup)")
    args = parser.parse_args()
    return run_tests(keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
