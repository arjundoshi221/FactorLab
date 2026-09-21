"""Create alt_political_us.legislator_aliases — manual override curation table.

Where the strict bioguide matcher cannot bridge a name (e.g. 'Nicholas V. Taylor'
files PTRs but bioguide stores him as `first='Van'` so the matcher's strict
first-name overlap rule fails), the override lives here as data, not as a
hardcoded list inside a Python script.

Phase G2 will wire `_bioguide.StrictBioguideMatcher` to consult this table as
Stage 0 (highest priority, beats fuzzy stages 1-3).

Seed: Van Taylor (TX-03) → T000479. Verified via 2022_20020767.pdf.

Revision ID: 024
Revises: 023
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "alt_political_us"


def upgrade() -> None:
    op.create_table(
        "legislator_aliases",
        sa.Column("country_code", sa.String(2), nullable=False, server_default="US"),
        sa.Column("alias_normalized", sa.String(200), nullable=False,
                  comment="Lowercase, stripped of punctuation; matches the matcher's "
                          "normalized input."),
        sa.Column("bioguide_id", sa.String(7), nullable=False),
        sa.Column("source", sa.String(50), nullable=False,
                  comment="'manual_pdf_review' | 'historical_yaml' | 'curated' ..."),
        sa.Column("confidence", sa.String(15), nullable=False,
                  comment="'verified' | 'high' | 'medium' — never insert 'low'."),
        sa.Column("verified_via", sa.Text, nullable=True,
                  comment="Pointer to evidence: PDF path, URL, doc_id, etc."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("country_code", "alias_normalized",
                                name="pk_legislator_aliases"),
        sa.ForeignKeyConstraint(["country_code"], ["ref.countries.code"],
                                name="fk_legislator_aliases_country"),
        sa.ForeignKeyConstraint(["bioguide_id"], [f"{SCHEMA}.legislators.bioguide_id"],
                                name="fk_legislator_aliases_bioguide",
                                ondelete="RESTRICT"),
        sa.CheckConstraint(
            "confidence IN ('verified','high','medium')",
            name="confidence",
        ),
        schema=SCHEMA,
        comment="Manual / curated alias overrides for the bioguide matcher. "
                "Stage 0 priority — beats all fuzzy matching.",
    )
    op.create_index("ix_legislator_aliases_bioguide", "legislator_aliases",
                    ["bioguide_id"], schema=SCHEMA)

    # Seed Van Taylor — historical override that previously lived inside
    # scripts/rescue_house_clerk_tier2.py:39-45 as a Python list.
    op.bulk_insert(
        sa.table(
            "legislator_aliases",
            sa.column("country_code"), sa.column("alias_normalized"),
            sa.column("bioguide_id"), sa.column("source"),
            sa.column("confidence"), sa.column("verified_via"),
            schema=SCHEMA,
        ),
        [
            {
                "country_code": "US",
                "alias_normalized": "nicholas v. taylor",
                "bioguide_id": "T000479",
                "source": "manual_pdf_review",
                "confidence": "verified",
                "verified_via": (
                    "House Clerk PTR 2022_20020767.pdf (TX-03 State/District). "
                    "Bioguide stores him as first='Van'; PTRs file as "
                    "'Nicholas V. Taylor' so the strict matcher cannot bridge."
                ),
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_legislator_aliases_bioguide",
                  table_name="legislator_aliases", schema=SCHEMA)
    op.drop_table("legislator_aliases", schema=SCHEMA)
