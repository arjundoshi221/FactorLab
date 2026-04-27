"""Add futures columns to ref.securities and market.price_bars_minute.

Revision ID: 002
Revises: 001
Create Date: 2026-04-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- ref.securities: futures/derivative metadata --
    op.add_column("securities", sa.Column("segment", sa.String(20), nullable=True,
                  comment="NSE_EQ, NSE_FO, BSE_EQ, etc."), schema="ref")
    op.add_column("securities", sa.Column("underlying_symbol", sa.String(50), nullable=True,
                  comment="For derivatives: underlying equity symbol (e.g. RELIANCE)"), schema="ref")
    op.add_column("securities", sa.Column("expiry", sa.Date(), nullable=True,
                  comment="Contract expiry date (NULL for equities)"), schema="ref")
    op.add_column("securities", sa.Column("lot_size", sa.Integer(), nullable=True,
                  comment="F&O lot size (NULL for equities)"), schema="ref")
    op.create_index("ix_securities_segment", "securities", ["segment"], schema="ref")
    op.create_index("ix_securities_underlying", "securities", ["underlying_symbol"], schema="ref")

    # -- market.price_bars_minute: instrument key + segment for equity/futures coexistence --
    op.add_column("price_bars_minute", sa.Column("instrument_key", sa.String(100), nullable=True,
                  comment="Vendor canonical key e.g. NSE_FO|67003"), schema="market")
    op.add_column("price_bars_minute", sa.Column("segment", sa.String(20), nullable=True,
                  comment="NSE_EQ, NSE_FO — differentiates equity vs futures bars"), schema="market")
    op.create_index("ix_price_minute_segment", "price_bars_minute", ["segment"], schema="market")
    op.create_index("ix_price_minute_instrument", "price_bars_minute", ["instrument_key"], schema="market")


def downgrade() -> None:
    op.drop_index("ix_price_minute_instrument", table_name="price_bars_minute", schema="market")
    op.drop_index("ix_price_minute_segment", table_name="price_bars_minute", schema="market")
    op.drop_column("price_bars_minute", "segment", schema="market")
    op.drop_column("price_bars_minute", "instrument_key", schema="market")

    op.drop_index("ix_securities_underlying", table_name="securities", schema="ref")
    op.drop_index("ix_securities_segment", table_name="securities", schema="ref")
    op.drop_column("securities", "lot_size", schema="ref")
    op.drop_column("securities", "expiry", schema="ref")
    op.drop_column("securities", "underlying_symbol", schema="ref")
    op.drop_column("securities", "segment", schema="ref")
