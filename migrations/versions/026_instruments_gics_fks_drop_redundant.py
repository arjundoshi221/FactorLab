"""Wire GICS FKs on ref.instruments + drop transitively-redundant columns.

Two related changes (BCNF compliance + flagship-query bridge):

1. Replace `instruments.sector` (freeform String, currently 0 populated rows)
   with proper FKs:
     instruments.gics_sector_id    INT FK ref.gics_sectors(id)
     instruments.gics_industry_id  INT FK ref.gics_industries(id)
   Both nullable until US fundamentals backfill populates them. Wires the
   `committee_sector_map.gics_code` ↔ instrument-side join for the flagship
   conjunction query (verify_political.py rewrite is in Phase G6).

2. Drop `instruments.country_code` and `instruments.currency_code`. Both are
   transitively derivable from `instruments.market_code → ref.markets`
   (one-hop JOIN). Strict BCNF: keeping them allows drift between
   `instruments.country_code` and `markets.country_code` for the same row.
   Verified pre-flight: 0 FKs from anywhere reference these columns directly.

Revision ID: 026
Revises: 025
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "ref"


def upgrade() -> None:
    # 1. Add the GICS FK columns (nullable; backfilled later)
    op.add_column("instruments",
                  sa.Column("gics_sector_id", sa.Integer, nullable=True),
                  schema=SCHEMA)
    op.add_column("instruments",
                  sa.Column("gics_industry_id", sa.Integer, nullable=True),
                  schema=SCHEMA)
    op.create_foreign_key(
        "fk_instruments_gics_sector_id_gics_sectors",
        "instruments", "gics_sectors",
        ["gics_sector_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_instruments_gics_industry_id_gics_industries",
        "instruments", "gics_industries",
        ["gics_industry_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA,
    )
    op.create_index("ix_instruments_gics_sector_id", "instruments",
                    ["gics_sector_id"], schema=SCHEMA,
                    postgresql_where=sa.text("gics_sector_id IS NOT NULL"))
    op.create_index("ix_instruments_gics_industry_id", "instruments",
                    ["gics_industry_id"], schema=SCHEMA,
                    postgresql_where=sa.text("gics_industry_id IS NOT NULL"))

    # 2. Drop the freeform `sector` String (0 rows populated, verified pre-flight)
    op.drop_index("ix_instruments_sector", table_name="instruments",
                  schema=SCHEMA, if_exists=True)
    op.drop_column("instruments", "sector", schema=SCHEMA)

    # 3. Drop the BCNF-redundant `country_code` + `currency_code` columns.
    #    Pre-flight verified: 0 inbound FKs reference these directly.
    #    Existing FKs on these columns (instruments → ref.countries/currencies)
    #    are dropped automatically by drop_column.
    op.drop_index("ix_instruments_country", table_name="instruments",
                  schema=SCHEMA, if_exists=True)
    op.drop_column("instruments", "country_code", schema=SCHEMA)
    op.drop_column("instruments", "currency_code", schema=SCHEMA)


def downgrade() -> None:
    # Re-add the dropped columns (they'll be NULL — original data not recoverable)
    op.add_column("instruments",
                  sa.Column("currency_code", sa.String(3), nullable=True),
                  schema=SCHEMA)
    op.add_column("instruments",
                  sa.Column("country_code", sa.String(2), nullable=True),
                  schema=SCHEMA)
    op.add_column("instruments",
                  sa.Column("sector", sa.String(100), nullable=True),
                  schema=SCHEMA)
    op.create_index("ix_instruments_country", "instruments",
                    ["country_code"], schema=SCHEMA)

    op.drop_index("ix_instruments_gics_industry_id",
                  table_name="instruments", schema=SCHEMA, if_exists=True)
    op.drop_index("ix_instruments_gics_sector_id",
                  table_name="instruments", schema=SCHEMA, if_exists=True)
    op.drop_constraint("fk_instruments_gics_industry_id_gics_industries",
                       "instruments", schema=SCHEMA)
    op.drop_constraint("fk_instruments_gics_sector_id_gics_sectors",
                       "instruments", schema=SCHEMA)
    op.drop_column("instruments", "gics_industry_id", schema=SCHEMA)
    op.drop_column("instruments", "gics_sector_id", schema=SCHEMA)
