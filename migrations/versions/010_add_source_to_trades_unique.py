"""Add `source` to legislator_trades UNIQUE constraint.

Original constraint:
    (country_code, chamber, filing_id, transaction_date,
     asset_name_raw, transaction_type, amount_str)

Problem: when the same logical trade is captured by two sources
(house_clerk_ptr + senate_stock_watcher_historical, or eFD + a future
Quiver mirror), they collide on this key. We want to preserve every source's
own row — cross-source dedup happens at query time, not at storage time.

New constraint:
    (source, country_code, chamber, filing_id, transaction_date,
     asset_name_raw, transaction_type, amount_str)

Revision ID: 010
Revises: 009
Create Date: 2026-04-30
"""

from typing import Sequence, Union

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "alt_political"


def upgrade() -> None:
    op.drop_constraint("uq_legislator_trades_dedup", "legislator_trades", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_legislator_trades_dedup",
        "legislator_trades",
        ["source", "country_code", "chamber", "filing_id", "transaction_date",
         "asset_name_raw", "transaction_type", "amount_str"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("uq_legislator_trades_dedup", "legislator_trades", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_legislator_trades_dedup",
        "legislator_trades",
        ["country_code", "chamber", "filing_id", "transaction_date",
         "asset_name_raw", "transaction_type", "amount_str"],
        schema=SCHEMA,
    )
