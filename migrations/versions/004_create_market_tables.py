"""Create market tables: candles_1min (hypertable), candles_daily (hypertable), raw_responses.

Revision ID: 004
Revises: 003
Create Date: 2026-04-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "market"
REF = "ref"


def upgrade() -> None:
    # -- market.candles_1min --
    op.create_table(
        "candles_1min",
        sa.Column("instrument_id", UUID(as_uuid=True), sa.ForeignKey(f"{REF}.instruments.id"), nullable=False),
        sa.Column("contract_id", UUID(as_uuid=True), sa.ForeignKey(f"{REF}.contracts.id"), nullable=True),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("volume", sa.BigInteger),
        sa.Column("oi", sa.BigInteger, server_default="0", comment="Open interest (0 for EQ)"),
        sa.Column("source", sa.String(20), nullable=False, comment="upstox, ibkr"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
        comment="1-min OHLCV. TimescaleDB hypertable on bar_time, chunk=1day, compress after 7d.",
    )

    # Partial unique indexes: EQ vs FUT dedup
    op.execute("""
        CREATE UNIQUE INDEX ix_candles_1min_eq
        ON market.candles_1min (instrument_id, bar_time)
        WHERE contract_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX ix_candles_1min_fut
        ON market.candles_1min (contract_id, bar_time)
        WHERE contract_id IS NOT NULL
    """)
    op.create_index("ix_candles_1min_time", "candles_1min", ["bar_time"], schema=SCHEMA)

    # TimescaleDB hypertable
    op.execute("""
        SELECT create_hypertable(
            'market.candles_1min', 'bar_time',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE
        )
    """)

    # -- market.candles_daily --
    op.create_table(
        "candles_daily",
        sa.Column("instrument_id", UUID(as_uuid=True), sa.ForeignKey(f"{REF}.instruments.id"), nullable=False),
        sa.Column("contract_id", UUID(as_uuid=True), sa.ForeignKey(f"{REF}.contracts.id"), nullable=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("adj_close", sa.Numeric(18, 6), comment="NULL for FUT"),
        sa.Column("volume", sa.BigInteger),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
        comment="Daily OHLCV. TimescaleDB hypertable on trade_date, chunk=1month.",
    )

    # Partial unique indexes: EQ vs FUT dedup
    op.execute("""
        CREATE UNIQUE INDEX ix_candles_daily_eq
        ON market.candles_daily (instrument_id, trade_date)
        WHERE contract_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX ix_candles_daily_fut
        ON market.candles_daily (contract_id, trade_date)
        WHERE contract_id IS NOT NULL
    """)
    op.create_index("ix_candles_daily_date", "candles_daily", ["trade_date"], schema=SCHEMA)

    # TimescaleDB hypertable
    op.execute("""
        SELECT create_hypertable(
            'market.candles_daily', 'trade_date',
            chunk_time_interval => INTERVAL '1 month',
            if_not_exists => TRUE
        )
    """)

    # -- market.raw_responses --
    op.create_table(
        "raw_responses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.String(30), nullable=False, comment="upstox, eodhd, ibkr"),
        sa.Column("endpoint", sa.String(200), nullable=False, comment="API URL path"),
        sa.Column("instrument_key", sa.String(100), nullable=True, comment="For lookup"),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=True, comment="Candles in response"),
        sa.Column("parsed_into", sa.String(50), nullable=True, comment="Target table name"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_raw_responses_fetched", "raw_responses", ["fetched_at"], schema=SCHEMA)
    op.create_index("ix_raw_responses_instrument", "raw_responses", ["instrument_key"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_raw_responses_instrument", table_name="raw_responses", schema=SCHEMA)
    op.drop_index("ix_raw_responses_fetched", table_name="raw_responses", schema=SCHEMA)
    op.drop_table("raw_responses", schema=SCHEMA)

    op.drop_index("ix_candles_daily_date", table_name="candles_daily", schema=SCHEMA)
    op.execute("DROP INDEX IF EXISTS market.ix_candles_daily_fut")
    op.execute("DROP INDEX IF EXISTS market.ix_candles_daily_eq")
    op.drop_table("candles_daily", schema=SCHEMA)

    op.drop_index("ix_candles_1min_time", table_name="candles_1min", schema=SCHEMA)
    op.execute("DROP INDEX IF EXISTS market.ix_candles_1min_fut")
    op.execute("DROP INDEX IF EXISTS market.ix_candles_1min_eq")
    op.drop_table("candles_1min", schema=SCHEMA)
