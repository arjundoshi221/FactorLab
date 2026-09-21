"""Add session column to candles_1min + create candles_5min hypertable.

Both tables get a ``session VARCHAR(8)`` column tagging each bar with the trading
session it belongs to (``pre`` | ``regular`` | ``post``). Existing rows in
``candles_1min`` (Upstox/IBKR India bars) are backfilled to ``'regular'`` since
Indian markets don't have post-market trading.

The new ``candles_5min`` table mirrors ``candles_1min``'s shape so the same
ingest helpers and the same TimescaleDB chunk policy apply.

Why VARCHAR not ENUM: Postgres ENUMs are awkward to extend (older versions
require non-transactional ALTER TYPE). VARCHAR(8) keeps schema evolution
trivial and is virtually free under TimescaleDB compression.

See ``docs/countries/us-equities.md`` for the rationale.

Revision ID: 011
Revises: 010
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "market"
REF = "ref"


def upgrade() -> None:
    # ── Add session column to candles_1min, backfill, set default ───────────
    op.add_column(
        "candles_1min",
        sa.Column(
            "session", sa.String(8), nullable=True,
            comment="Trading session: pre / regular / post (regular for non-US markets)",
        ),
        schema=SCHEMA,
    )
    op.execute(f"UPDATE {SCHEMA}.candles_1min SET session = 'regular' WHERE session IS NULL")
    op.alter_column(
        "candles_1min", "session",
        nullable=False,
        server_default="regular",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_candles_1min_session",
        "candles_1min",
        "session IN ('pre', 'regular', 'post')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_candles_1min_session_time",
        "candles_1min",
        ["session", "bar_time"],
        schema=SCHEMA,
    )

    # ── Create candles_5min ─────────────────────────────────────────────────
    op.create_table(
        "candles_5min",
        sa.Column("instrument_id", UUID(as_uuid=True), sa.ForeignKey(f"{REF}.instruments.id"), nullable=False),
        sa.Column("contract_id", UUID(as_uuid=True), sa.ForeignKey(f"{REF}.contracts.id"), nullable=True),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session", sa.String(8), nullable=False, server_default="regular",
                  comment="pre / regular / post"),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("volume", sa.BigInteger),
        sa.Column("oi", sa.BigInteger, server_default="0", comment="Open interest (0 for EQ)"),
        sa.Column("source", sa.String(20), nullable=False, comment="schwab, ibkr, upstox"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
        comment="5-min OHLCV. TimescaleDB hypertable on bar_time, chunk=7days, compress after 30d.",
    )

    # Partial unique indexes: EQ vs FUT dedup
    op.execute(f"""
        CREATE UNIQUE INDEX ix_candles_5min_eq
        ON {SCHEMA}.candles_5min (instrument_id, bar_time)
        WHERE contract_id IS NULL
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX ix_candles_5min_fut
        ON {SCHEMA}.candles_5min (contract_id, bar_time)
        WHERE contract_id IS NOT NULL
    """)
    op.create_index("ix_candles_5min_time", "candles_5min", ["bar_time"], schema=SCHEMA)
    op.create_index("ix_candles_5min_session_time", "candles_5min", ["session", "bar_time"], schema=SCHEMA)
    op.create_check_constraint(
        "ck_candles_5min_session",
        "candles_5min",
        "session IN ('pre', 'regular', 'post')",
        schema=SCHEMA,
    )

    # TimescaleDB hypertable -- 5-min data accumulates ~5x faster than 1-min/day per symbol
    # but ~12x slower than 1-min, so use a longer chunk window (7 days)
    op.execute(f"""
        SELECT create_hypertable(
            '{SCHEMA}.candles_5min', 'bar_time',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
    """)


def downgrade() -> None:
    op.drop_index("ix_candles_5min_session_time", table_name="candles_5min", schema=SCHEMA)
    op.drop_index("ix_candles_5min_time", table_name="candles_5min", schema=SCHEMA)
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_candles_5min_fut")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_candles_5min_eq")
    op.drop_table("candles_5min", schema=SCHEMA)

    op.drop_index("ix_candles_1min_session_time", table_name="candles_1min", schema=SCHEMA)
    op.drop_constraint("ck_candles_1min_session", "candles_1min", schema=SCHEMA, type_="check")
    op.drop_column("candles_1min", "session", schema=SCHEMA)
