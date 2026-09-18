"""Route market_in.candles_{1min,5min,daily} into split fact tables, drop old.

Routing rule (verified by data inspection 2026-05-01):
  contract_id IS NOT NULL                                  -> fact_futures
  contract_id IS NULL AND instrument_type IN ('EQ','ETF')  -> fact_equity
  contract_id IS NULL AND instrument_type = 'INDEX'        -> fact_index
  anything else                                            -> raise

Live-data shape on candles_1min (529,035 rows):
  contract_id NOT NULL  (futures): 263,043 rows -> fact_futures
  contract_id NULL + EQ (spot):    265,992 rows -> fact_equity
  candles_5min, candles_daily: 0 rows each

Per-asset-class verification gate before dropping source tables:
  fact_equity + fact_index + fact_futures must equal source row totals.

Resolution at insert time:
  freq_id      = ref.frequencies.id  WHERE code in ('1m','5m','1d')
  endpoint_id  = ref.data_endpoints.id WHERE code = old `source` string
                 (currently always 'upstox' — vendor-level catch-all endpoint)

Drops afterward:
  market_in.candles_1min, market_in.candles_5min, market_in.candles_daily

Revision ID: 022
Revises: 021
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Map source candle table -> ref.frequencies.code
SOURCE_FREQ = [
    ("candles_1min", "1m"),
    ("candles_5min", "5m"),
    ("candles_daily", "1d"),
]


def upgrade() -> None:
    conn = op.get_bind()

    # ── Pre-flight checks ────────────────────────────────────────────────
    # 1. All freq codes resolve
    for _, code in SOURCE_FREQ:
        n = conn.execute(sa.text(
            "SELECT count(*) FROM ref.frequencies WHERE code=:c"
        ), {"c": code}).scalar()
        if n != 1:
            raise RuntimeError(f"ref.frequencies missing code={code!r}")

    # 2. Every distinct source string in candles_* maps to an endpoint code
    for tbl, _ in SOURCE_FREQ:
        unmapped = conn.execute(sa.text(f"""
            SELECT DISTINCT source FROM market_in.{tbl}
            WHERE NOT EXISTS (
                SELECT 1 FROM ref.data_endpoints e WHERE e.code = source
            )
        """)).fetchall()
        if unmapped:
            raise RuntimeError(
                f"market_in.{tbl} has unmapped source values: "
                f"{[r[0] for r in unmapped]}. Add catch-all endpoints first."
            )

    # 3. Every candle row's instrument_type is recognized (no orphans)
    bad_types = conn.execute(sa.text("""
        SELECT DISTINCT i.instrument_type
        FROM market_in.candles_1min c
        JOIN ref.instruments i ON i.id = c.instrument_id
        WHERE c.contract_id IS NULL
          AND i.instrument_type NOT IN ('EQ','ETF','INDEX')
    """)).fetchall()
    if bad_types:
        raise RuntimeError(
            f"candles_1min has spot rows with unhandled instrument_type: "
            f"{[r[0] for r in bad_types]}"
        )

    # ── Capture pre-move counts for parity verification ──────────────────
    pre_counts = {}
    for tbl, _ in SOURCE_FREQ:
        pre_counts[tbl] = conn.execute(sa.text(
            f"SELECT count(*) FROM market_in.{tbl}"
        )).scalar()
    pre_total = sum(pre_counts.values())
    print(f"[022] pre-move totals: {pre_counts}  total={pre_total}")

    # ── Per-source-table routing ─────────────────────────────────────────
    # Note: candles_5min has `session` column (added in mig 011); 1min/daily don't.
    # We handle that with COALESCE on the SELECT side.

    for tbl, freq_code in SOURCE_FREQ:
        n_pre = pre_counts[tbl]
        if n_pre == 0:
            continue
        print(f"[022] routing market_in.{tbl} ({n_pre} rows) -> fact_*")

        # session column exists only on candles_5min (post mig 011)
        session_select = "c.session" if tbl == "candles_5min" else "'regular'"

        # adj_close column exists only on candles_daily
        adj_close_eq = "c.adj_close" if tbl == "candles_daily" else "NULL"

        # candles_5min has session col; candles_1min has volume + oi; candles_daily has adj_close + volume.
        # All three have: instrument_id, contract_id, open/high/low/close, source, ingested_at.
        # candles_1min/5min have bar_time; candles_daily has trade_date which becomes bar_time at midnight.
        time_select = "c.bar_time" if tbl != "candles_daily" else "c.trade_date::timestamptz"

        # 1. fact_equity: contract_id NULL AND type in (EQ, ETF)
        conn.execute(sa.text(f"""
            INSERT INTO market_in.fact_equity
                (instrument_id, freq_id, bar_time, open, high, low, close,
                 adj_close, volume, session, endpoint_id, ingested_at)
            SELECT
                c.instrument_id,
                (SELECT id FROM ref.frequencies WHERE code = :freq_code),
                {time_select},
                c.open, c.high, c.low, c.close,
                {adj_close_eq},
                c.volume,
                {session_select},
                (SELECT id FROM ref.data_endpoints WHERE code = c.source),
                c.ingested_at
            FROM market_in.{tbl} c
            JOIN ref.instruments i ON i.id = c.instrument_id
            WHERE c.contract_id IS NULL
              AND i.instrument_type IN ('EQ','ETF')
        """), {"freq_code": freq_code})

        # 2. fact_index: contract_id NULL AND type = INDEX
        conn.execute(sa.text(f"""
            INSERT INTO market_in.fact_index
                (instrument_id, freq_id, bar_time, open, high, low, close,
                 volume, session, endpoint_id, ingested_at)
            SELECT
                c.instrument_id,
                (SELECT id FROM ref.frequencies WHERE code = :freq_code),
                {time_select},
                c.open, c.high, c.low, c.close,
                c.volume,
                {session_select},
                (SELECT id FROM ref.data_endpoints WHERE code = c.source),
                c.ingested_at
            FROM market_in.{tbl} c
            JOIN ref.instruments i ON i.id = c.instrument_id
            WHERE c.contract_id IS NULL
              AND i.instrument_type = 'INDEX'
        """), {"freq_code": freq_code})

        # 3. fact_futures: contract_id NOT NULL
        # candles_1min has 'oi'; candles_5min has 'oi' (mig 011); candles_daily does NOT have oi.
        oi_select = "c.oi" if tbl in ("candles_1min", "candles_5min") else "0"
        conn.execute(sa.text(f"""
            INSERT INTO market_in.fact_futures
                (contract_id, instrument_id, freq_id, bar_time, open, high, low, close,
                 volume, oi, session, endpoint_id, ingested_at)
            SELECT
                c.contract_id,
                c.instrument_id,
                (SELECT id FROM ref.frequencies WHERE code = :freq_code),
                {time_select},
                c.open, c.high, c.low, c.close,
                c.volume, {oi_select},
                {session_select},
                (SELECT id FROM ref.data_endpoints WHERE code = c.source),
                c.ingested_at
            FROM market_in.{tbl} c
            WHERE c.contract_id IS NOT NULL
        """), {"freq_code": freq_code})

    # ── Parity gate ──────────────────────────────────────────────────────
    n_eq = conn.execute(sa.text(
        "SELECT count(*) FROM market_in.fact_equity")).scalar()
    n_idx = conn.execute(sa.text(
        "SELECT count(*) FROM market_in.fact_index")).scalar()
    n_fut = conn.execute(sa.text(
        "SELECT count(*) FROM market_in.fact_futures")).scalar()
    n_fact_total = n_eq + n_idx + n_fut
    print(f"[022] post-move per-class: equity={n_eq} index={n_idx} futures={n_fut} "
          f"total={n_fact_total}")

    if n_fact_total != pre_total:
        raise RuntimeError(
            f"Row-count parity FAIL: source total={pre_total}, "
            f"fact total={n_fact_total} (equity={n_eq} index={n_idx} futures={n_fut}). "
            f"Refusing to drop source tables."
        )

    # ── Drop the legacy candle tables ────────────────────────────────────
    # Order: drop indexes / hypertable / table for each. TimescaleDB DROP TABLE
    # cleans up its own catalog rows automatically.
    for tbl, _ in SOURCE_FREQ:
        op.drop_table(tbl, schema="market_in")

    print(f"[022] dropped legacy: " + ", ".join(
        f"market_in.{t}" for t, _ in SOURCE_FREQ))


def downgrade() -> None:
    """Recreate candles_1min/5min/daily and split rows back. Best-effort."""
    # Recreating with TimescaleDB hypertable conversion is complex; we only
    # support a forward-only path for this redesign. Manual rollback would
    # require running migrations 004 + 011 in reverse + repopulating from
    # the fact tables.
    raise NotImplementedError(
        "Migration 022 (candles -> split facts) is forward-only. "
        "To revert: alembic downgrade 021 (drops fact tables, no data "
        "recovery), then manually replay 004+011 and reroute via "
        "factorlab.storage.ingest helpers."
    )
