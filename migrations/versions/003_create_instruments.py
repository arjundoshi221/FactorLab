"""Create ref.instruments, ref.contracts, ref.instrument_daily.

Revision ID: 003
Revises: 002
Create Date: 2026-04-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "ref"


def upgrade() -> None:
    # -- ref.instruments --
    op.create_table(
        "instruments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("exchange_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.exchanges.id"), nullable=False),
        sa.Column("instrument_key", sa.String(100), nullable=False, unique=True,
                  comment="Vendor canonical: NSE_EQ|INE002A01018, AAPL.US"),
        sa.Column("trading_symbol", sa.String(50), nullable=False, comment="RELIANCE, AAPL"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("isin", sa.String(12), nullable=True, comment="ISIN (NULL for INDEX)"),
        sa.Column("segment", sa.String(20), nullable=False, comment="NSE_EQ, NSE_INDEX, US_EQ"),
        sa.Column("instrument_type", sa.String(10), nullable=False, comment="EQ, INDEX, ETF"),
        sa.Column("asset_class", sa.String(20), nullable=False, comment="equity, index, etf"),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
        sa.Column("market_code", sa.String(10), sa.ForeignKey(f"{SCHEMA}.markets.code"), nullable=False),
        sa.Column("currency_code", sa.String(3), sa.ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
        sa.Column("lot_size", sa.Integer, nullable=False, server_default="1"),
        sa.Column("tick_size", sa.Numeric(10, 2), nullable=True),
        sa.Column("freeze_quantity", sa.Numeric(12, 1), nullable=True),
        sa.Column("exchange_token", sa.String(20), nullable=True),
        sa.Column("security_type", sa.String(20), nullable=True, comment="NORMAL, etc."),
        sa.Column("sector", sa.String(100), nullable=True, comment="GICS sector or equivalent"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active",
                  comment="active, delisted, suspended"),
        sa.Column("first_seen", sa.Date, nullable=True),
        sa.Column("last_seen", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_instruments_symbol", "instruments", ["trading_symbol"], schema=SCHEMA)
    op.create_index("ix_instruments_segment", "instruments", ["segment"], schema=SCHEMA)
    op.create_index("ix_instruments_isin", "instruments", ["isin"], schema=SCHEMA)
    op.create_index("ix_instruments_country", "instruments", ["country_code"], schema=SCHEMA)
    op.create_index("ix_instruments_market", "instruments", ["market_code"], schema=SCHEMA)
    op.create_index("ix_instruments_status", "instruments", ["status"], schema=SCHEMA)

    # -- ref.contracts --
    op.create_table(
        "contracts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("instrument_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.instruments.id"), nullable=False,
                  comment="The underlying: RELIANCE, NIFTY"),
        sa.Column("exchange_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.exchanges.id"), nullable=False),
        sa.Column("contract_key", sa.String(100), nullable=False, unique=True, comment="Vendor key: NSE_FO|67003"),
        sa.Column("trading_symbol", sa.String(80), nullable=False, comment="RELIANCE FUT 26 APR 26"),
        sa.Column("contract_type", sa.String(10), nullable=False, comment="FUT, CE, PE"),
        sa.Column("segment", sa.String(20), nullable=False, comment="NSE_FO, BSE_FO"),
        sa.Column("expiry", sa.Date, nullable=False),
        sa.Column("strike_price", sa.Numeric(18, 2), nullable=True, comment="0 for FUT, strike for OPT"),
        sa.Column("lot_size", sa.Integer, nullable=False),
        sa.Column("tick_size", sa.Numeric(10, 2), nullable=True),
        sa.Column("freeze_quantity", sa.Numeric(12, 1), nullable=True),
        sa.Column("exchange_token", sa.String(20), nullable=True),
        sa.Column("weekly", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active", comment="active, expired"),
        sa.Column("first_seen", sa.Date, nullable=True),
        sa.Column("last_seen", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_contracts_instrument", "contracts", ["instrument_id"], schema=SCHEMA)
    op.create_index("ix_contracts_expiry", "contracts", ["expiry"], schema=SCHEMA)
    op.create_index("ix_contracts_segment", "contracts", ["segment"], schema=SCHEMA)
    op.create_index("ix_contracts_status", "contracts", ["status"], schema=SCHEMA)
    op.create_index("ix_contracts_nearest", "contracts", ["instrument_id", "expiry", "contract_type"], schema=SCHEMA)

    # -- ref.instrument_daily --
    op.create_table(
        "instrument_daily",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instrument_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.instruments.id"), nullable=False),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("lot_size", sa.Integer, nullable=True),
        sa.Column("freeze_quantity", sa.Numeric(12, 1), nullable=True),
        sa.Column("tick_size", sa.Numeric(10, 2), nullable=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instrument_id", "snapshot_date", name="uq_instrument_daily_id_date"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("instrument_daily", schema=SCHEMA)
    op.drop_index("ix_contracts_nearest", table_name="contracts", schema=SCHEMA)
    op.drop_index("ix_contracts_status", table_name="contracts", schema=SCHEMA)
    op.drop_index("ix_contracts_segment", table_name="contracts", schema=SCHEMA)
    op.drop_index("ix_contracts_expiry", table_name="contracts", schema=SCHEMA)
    op.drop_index("ix_contracts_instrument", table_name="contracts", schema=SCHEMA)
    op.drop_table("contracts", schema=SCHEMA)
    op.drop_index("ix_instruments_status", table_name="instruments", schema=SCHEMA)
    op.drop_index("ix_instruments_market", table_name="instruments", schema=SCHEMA)
    op.drop_index("ix_instruments_country", table_name="instruments", schema=SCHEMA)
    op.drop_index("ix_instruments_isin", table_name="instruments", schema=SCHEMA)
    op.drop_index("ix_instruments_segment", table_name="instruments", schema=SCHEMA)
    op.drop_index("ix_instruments_symbol", table_name="instruments", schema=SCHEMA)
    op.drop_table("instruments", schema=SCHEMA)
