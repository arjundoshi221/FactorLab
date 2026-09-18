"""Create ref.frequencies dim — replaces table-name-as-frequency with a proper FK.

Subsequent migrations will replace `market.candles_1min` / `candles_5min` /
`candles_daily` (3 tables) with split fact tables (`fact_equity` / `fact_index` /
`fact_futures`) keyed on a `freq_id` FK to this dim. Adding a new frequency
becomes an INSERT, not a schema change.

Revision ID: 013
Revises: 012
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "ref"


def upgrade() -> None:
    op.create_table(
        "frequencies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(8), nullable=False, unique=True,
                  comment="Canonical short code: 1m, 5m, 15m, 1h, 1d, 1w, 1M"),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("seconds", sa.Integer, nullable=False,
                  comment="Approximate bar duration in seconds (1d=86400, 1M=2592000)"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("seconds > 0", name="seconds_positive"),
        schema=SCHEMA,
        comment="Bar-frequency dim. FK target for fact tables' freq_id.",
    )
    op.create_index("ix_frequencies_code", "frequencies", ["code"], schema=SCHEMA)

    # Seed canonical frequencies. seconds is approximate for 1M (calendar months vary).
    op.bulk_insert(
        sa.table(
            "frequencies",
            sa.column("code"), sa.column("name"), sa.column("seconds"),
            schema=SCHEMA,
        ),
        [
            {"code": "1m",  "name": "1 minute",   "seconds": 60},
            {"code": "5m",  "name": "5 minutes",  "seconds": 300},
            {"code": "15m", "name": "15 minutes", "seconds": 900},
            {"code": "1h",  "name": "1 hour",     "seconds": 3600},
            {"code": "1d",  "name": "1 day",      "seconds": 86400},
            {"code": "1w",  "name": "1 week",     "seconds": 604800},
            {"code": "1M",  "name": "1 month",    "seconds": 2592000},
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_frequencies_code", table_name="frequencies", schema=SCHEMA)
    op.drop_table("frequencies", schema=SCHEMA)
