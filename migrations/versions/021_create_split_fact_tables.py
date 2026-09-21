"""Create split fact tables in market_in + market_us (3 per schema, 6 total).

Replaces the freq-named tables (candles_1min / candles_5min / candles_daily)
with split-by-asset-class fact tables keyed on freq_id FK to ref.frequencies:

  fact_equity   stocks + ETFs        instrument_id FK; adj_close meaningful
  fact_index    NIFTY, S&P 500, etc.  instrument_id FK; no adj_close, no oi
  fact_futures  derivatives          contract_id NOT NULL + underlying instrument_id; oi meaningful

Common columns: freq_id FK, bar_time, OHLC, session, endpoint_id FK, ingested_at.
Per-class columns vary (see definitions below).

All 6 tables become TimescaleDB hypertables on bar_time, chunk=7 days
(compromise between 1m and 1d data densities).

Migration 022 routes the existing 529,035 rows from market_in.candles_*
into the new fact tables, then drops the old tables.

Revision ID: 021
Revises: 020
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Schemas to create the fact tables in (per-country market schemas)
TARGET_SCHEMAS = ["market_in", "market_us"]


def _create_fact_equity(schema: str) -> None:
    """fact_equity: stocks + ETFs. adj_close meaningful; no oi; no contract."""
    op.create_table(
        "fact_equity",
        sa.Column("instrument_id", UUID(as_uuid=True),
                  sa.ForeignKey("ref.instruments.id"), nullable=False),
        sa.Column("freq_id", sa.Integer,
                  sa.ForeignKey("ref.frequencies.id"), nullable=False),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("adj_close", sa.Numeric(18, 6),
                  comment="Split/dividend-adjusted close. NULL for intraday."),
        sa.Column("volume", sa.BigInteger),
        sa.Column("session", sa.String(8), nullable=False, server_default="regular",
                  comment="pre / regular / post (US extended hours; IN always 'regular')"),
        sa.Column("endpoint_id", sa.Integer,
                  sa.ForeignKey("ref.data_endpoints.id"), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("session IN ('pre', 'regular', 'post')",
                           name="session"),
        schema=schema,
        comment="Stocks + ETFs OHLCV bars (any frequency via freq_id). "
                "TimescaleDB hypertable on bar_time, chunk=7 days.",
    )
    # Unique idx for (instrument, freq, bar) dedup
    op.create_index(f"ix_{schema}_fact_equity_uniq", "fact_equity",
                    ["instrument_id", "freq_id", "bar_time"],
                    unique=True, schema=schema)
    op.create_index(f"ix_{schema}_fact_equity_freq_time", "fact_equity",
                    ["freq_id", "bar_time"], schema=schema)
    op.create_index(f"ix_{schema}_fact_equity_endpoint", "fact_equity",
                    ["endpoint_id"], schema=schema)
    op.execute(f"""
        SELECT create_hypertable(
            '{schema}.fact_equity', 'bar_time',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
    """)


def _create_fact_index(schema: str) -> None:
    """fact_index: indices. No adj_close, no oi. volume often NULL."""
    op.create_table(
        "fact_index",
        sa.Column("instrument_id", UUID(as_uuid=True),
                  sa.ForeignKey("ref.instruments.id"), nullable=False),
        sa.Column("freq_id", sa.Integer,
                  sa.ForeignKey("ref.frequencies.id"), nullable=False),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("volume", sa.BigInteger,
                  comment="Index volume — often NULL; some vendors report broad-market totals."),
        sa.Column("session", sa.String(8), nullable=False, server_default="regular"),
        sa.Column("endpoint_id", sa.Integer,
                  sa.ForeignKey("ref.data_endpoints.id"), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("session IN ('pre', 'regular', 'post')",
                           name="session"),
        schema=schema,
        comment="Index OHLC bars (NIFTY, S&P 500, etc.). "
                "TimescaleDB hypertable on bar_time, chunk=7 days.",
    )
    op.create_index(f"ix_{schema}_fact_index_uniq", "fact_index",
                    ["instrument_id", "freq_id", "bar_time"],
                    unique=True, schema=schema)
    op.create_index(f"ix_{schema}_fact_index_freq_time", "fact_index",
                    ["freq_id", "bar_time"], schema=schema)
    op.create_index(f"ix_{schema}_fact_index_endpoint", "fact_index",
                    ["endpoint_id"], schema=schema)
    op.execute(f"""
        SELECT create_hypertable(
            '{schema}.fact_index', 'bar_time',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
    """)


def _create_fact_futures(schema: str) -> None:
    """fact_futures: derivatives. contract_id NOT NULL; oi meaningful; no adj_close."""
    op.create_table(
        "fact_futures",
        sa.Column("contract_id", UUID(as_uuid=True),
                  sa.ForeignKey("ref.contracts.id"), nullable=False,
                  comment="The specific futures/options contract."),
        sa.Column("instrument_id", UUID(as_uuid=True),
                  sa.ForeignKey("ref.instruments.id"), nullable=False,
                  comment="The underlying instrument (RELIANCE, NIFTY)."),
        sa.Column("freq_id", sa.Integer,
                  sa.ForeignKey("ref.frequencies.id"), nullable=False),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("volume", sa.BigInteger),
        sa.Column("oi", sa.BigInteger, nullable=False, server_default="0",
                  comment="Open interest at bar close."),
        sa.Column("session", sa.String(8), nullable=False, server_default="regular"),
        sa.Column("endpoint_id", sa.Integer,
                  sa.ForeignKey("ref.data_endpoints.id"), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("session IN ('pre', 'regular', 'post')",
                           name="session"),
        schema=schema,
        comment="Derivatives contract OHLCV+OI bars. "
                "TimescaleDB hypertable on bar_time, chunk=7 days.",
    )
    op.create_index(f"ix_{schema}_fact_futures_uniq", "fact_futures",
                    ["contract_id", "freq_id", "bar_time"],
                    unique=True, schema=schema)
    op.create_index(f"ix_{schema}_fact_futures_underlying_time", "fact_futures",
                    ["instrument_id", "bar_time"], schema=schema)
    op.create_index(f"ix_{schema}_fact_futures_freq_time", "fact_futures",
                    ["freq_id", "bar_time"], schema=schema)
    op.create_index(f"ix_{schema}_fact_futures_endpoint", "fact_futures",
                    ["endpoint_id"], schema=schema)
    op.execute(f"""
        SELECT create_hypertable(
            '{schema}.fact_futures', 'bar_time',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
    """)


def upgrade() -> None:
    for schema in TARGET_SCHEMAS:
        _create_fact_equity(schema)
        _create_fact_index(schema)
        _create_fact_futures(schema)


def downgrade() -> None:
    for schema in reversed(TARGET_SCHEMAS):
        for tbl in ("fact_futures", "fact_index", "fact_equity"):
            op.drop_table(tbl, schema=schema)
