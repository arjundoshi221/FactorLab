"""Widen alt_political.legislator_terms.party from varchar(20) to varchar(50).

Historical parties like 'States Rights Democrat' (22) and
'Democratic-Republican' (21) exceed the original 20-char width.

Revision ID: 009
Revises: 008
Create Date: 2026-04-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "legislator_terms", "party",
        existing_type=sa.String(20),
        type_=sa.String(50),
        existing_nullable=True,
        schema="alt_political",
    )


def downgrade() -> None:
    op.alter_column(
        "legislator_terms", "party",
        existing_type=sa.String(50),
        type_=sa.String(20),
        existing_nullable=True,
        schema="alt_political",
    )
