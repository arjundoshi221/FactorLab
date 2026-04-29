"""Create ref dimension tables: countries, currencies, fx_pairs, fx_rates_daily, markets, exchanges.

Revision ID: 002
Revises: 001
Create Date: 2026-04-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "ref"


def upgrade() -> None:
    # -- ref.countries --
    op.create_table(
        "countries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(2), nullable=False, unique=True, comment="ISO 3166-1 alpha-2: IN, US, SG"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("region", sa.String(20), nullable=False, comment="asia, americas, europe"),
        sa.Column("timezone", sa.String(50), nullable=False, comment="Primary IANA timezone"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    # -- ref.currencies --
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(3), primary_key=True, comment="ISO 4217: INR, USD, SGD"),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("symbol", sa.String(5), nullable=False, comment="Currency symbol"),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    # -- ref.fx_pairs --
    op.create_table(
        "fx_pairs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("base", sa.String(3), sa.ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
        sa.Column("quote", sa.String(3), sa.ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
        sa.Column("pair_code", sa.String(7), nullable=False, unique=True, comment="INRUSD, USDINR, INRSGD"),
        sa.Column("source", sa.String(30), nullable=False, comment="ecb, rbi, upstox"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("base", "quote", name="uq_fx_pairs_base_quote"),
        sa.CheckConstraint("base != quote", name="ck_fx_pairs_not_same"),
        schema=SCHEMA,
    )

    # -- ref.fx_rates_daily --
    op.create_table(
        "fx_rates_daily",
        sa.Column("pair_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.fx_pairs.id"), nullable=False, primary_key=True),
        sa.Column("rate_date", sa.Date, nullable=False, primary_key=True),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False, comment="1 base = X quote"),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_fx_rates_date", "fx_rates_daily", ["rate_date"], schema=SCHEMA)

    # -- ref.markets --
    op.create_table(
        "markets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(10), nullable=False, unique=True, comment="IND, USA, EUR, GBR, SGP"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
        sa.Column("currency_code", sa.String(3), sa.ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    # -- ref.exchanges --
    op.create_table(
        "exchanges",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True, comment="NSE, BSE, NYSE, NASDAQ"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("market_code", sa.String(10), sa.ForeignKey(f"{SCHEMA}.markets.code"), nullable=False),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(f"{SCHEMA}.countries.code"), nullable=False),
        sa.Column("currency_code", sa.String(3), sa.ForeignKey(f"{SCHEMA}.currencies.code"), nullable=False),
        sa.Column("timezone", sa.String(50), nullable=False, comment="IANA tz: Asia/Kolkata, America/New_York"),
        sa.Column("open_time", sa.String(8), nullable=True, comment="HH:MM:SS local"),
        sa.Column("close_time", sa.String(8), nullable=True, comment="HH:MM:SS local"),
        sa.Column("calendar_key", sa.String(20), nullable=True, comment="exchange_calendars key: XBOM, XNYS"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("exchanges", schema=SCHEMA)
    op.drop_table("markets", schema=SCHEMA)
    op.drop_index("ix_fx_rates_date", table_name="fx_rates_daily", schema=SCHEMA)
    op.drop_table("fx_rates_daily", schema=SCHEMA)
    op.drop_table("fx_pairs", schema=SCHEMA)
    op.drop_table("currencies", schema=SCHEMA)
    op.drop_table("countries", schema=SCHEMA)
