"""Create empty market_us schema — placeholder for future US price ingest.

US ingest paths (Schwab, EODHD daily) currently exist in code but are NOT
scheduled and NOT writing data. After Phase D's split-fact migration creates
`fact_equity` / `fact_index` / `fact_futures` in BOTH market_in and market_us,
the US sources can be wired to write to `market_us.fact_equity` etc.

This migration just creates the schema; no tables yet. Phase D's migration
021 creates the unified split-fact tables in both market schemas.

Revision ID: 020
Revises: 019
Create Date: 2026-05-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS market_us")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS market_us CASCADE")
