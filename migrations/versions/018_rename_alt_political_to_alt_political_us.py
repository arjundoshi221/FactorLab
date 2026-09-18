"""Rename alt_political schema -> alt_political_us for per-country separation.

Future India political data (SEBI insider trades, Lok Sabha disclosures) lands
in a separate alt_political_in schema; ref + audit remain shared.

Mechanically a single Postgres metadata operation:
  ALTER SCHEMA alt_political RENAME TO alt_political_us

All 25 tables, internal FKs, indexes, sequences, check constraints, unique
constraints inherit the new namespace atomically. Outbound FKs to ref.* are
unaffected (RHS unchanged). 0 inbound FKs from outside the schema (verified
2026-05-01), 0 views, 0 functions — zero blast radius outside the rename.

`country_code` columns on every table KEPT transitional (tautological once
schema is country-specific, but removing them touches PKs/FKs across 25
tables; deferred to a future cleanup migration).

Revision ID: 018
Revises: 017
Create Date: 2026-05-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER SCHEMA alt_political RENAME TO alt_political_us")


def downgrade() -> None:
    op.execute("ALTER SCHEMA alt_political_us RENAME TO alt_political")
