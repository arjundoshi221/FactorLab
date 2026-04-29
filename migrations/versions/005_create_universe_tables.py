"""Create universe tables: indexes, members.

Revision ID: 005
Revises: 004
Create Date: 2026-04-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "universe"
REF = "ref"


def upgrade() -> None:
    # -- universe.indexes --
    op.create_table(
        "indexes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False, unique=True, comment="nifty50, sp500, fo_eligible"),
        sa.Column("display_name", sa.String(200), nullable=True, comment="NIFTY 50, S&P 500"),
        sa.Column("market_code", sa.String(10), sa.ForeignKey(f"{REF}.markets.code"), nullable=False),
        sa.Column("index_type", sa.String(30), nullable=False, server_default="benchmark",
                  comment="benchmark, custom, derived, sector"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    # -- universe.members --
    op.create_table(
        "members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("index_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.indexes.id"), nullable=False),
        sa.Column("instrument_id", UUID(as_uuid=True), sa.ForeignKey(f"{REF}.instruments.id"), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False, comment="Date instrument entered the index"),
        sa.Column("end_date", sa.Date, nullable=True, comment="Date left — NULL = current member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
        comment="Point-in-time membership. Query: WHERE start_date <= @as_of AND (end_date IS NULL OR end_date > @as_of)",
    )
    op.create_index("ix_members_index_date", "members", ["index_id", "start_date", "end_date"], schema=SCHEMA)
    op.create_index("ix_members_instrument_date", "members", ["instrument_id", "start_date", "end_date"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_members_instrument_date", table_name="members", schema=SCHEMA)
    op.drop_index("ix_members_index_date", table_name="members", schema=SCHEMA)
    op.drop_table("members", schema=SCHEMA)
    op.drop_table("indexes", schema=SCHEMA)
