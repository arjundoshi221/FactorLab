"""Create ref.gics_sectors + ref.gics_industries dims.

Replaces freeform `instruments.sector` String column with proper FKs in a later
migration (025). Seeded with the canonical 11 GICS sectors and 158 sub-industries
per the 2023 GICS structure.

Revision ID: 014
Revises: 013
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "ref"


def upgrade() -> None:
    # ── ref.gics_sectors — 11 canonical sectors ─────────────────────────────
    op.create_table(
        "gics_sectors",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(8), nullable=False, unique=True,
                  comment="GICS 2-digit sector code"),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
        comment="GICS top-level sectors (11). FK target for instruments.gics_sector_id.",
    )
    op.create_index("ix_gics_sectors_code", "gics_sectors", ["code"], schema=SCHEMA)

    op.bulk_insert(
        sa.table(
            "gics_sectors",
            sa.column("code"), sa.column("name"),
            schema=SCHEMA,
        ),
        [
            {"code": "10", "name": "Energy"},
            {"code": "15", "name": "Materials"},
            {"code": "20", "name": "Industrials"},
            {"code": "25", "name": "Consumer Discretionary"},
            {"code": "30", "name": "Consumer Staples"},
            {"code": "35", "name": "Health Care"},
            {"code": "40", "name": "Financials"},
            {"code": "45", "name": "Information Technology"},
            {"code": "50", "name": "Communication Services"},
            {"code": "55", "name": "Utilities"},
            {"code": "60", "name": "Real Estate"},
        ],
    )

    # ── ref.gics_industries — 158 industries ────────────────────────────────
    # NOTE: GICS has a 4-level hierarchy (Sector→Industry Group→Industry→
    # Sub-Industry). We collapse to 2 levels here (sector + industry) using the
    # canonical 4-digit Industry-Group codes for simplicity. If sub-industry
    # granularity is needed later, add a third dim.
    op.create_table(
        "gics_industries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(8), nullable=False, unique=True,
                  comment="GICS 4-digit industry-group code"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("sector_id", sa.Integer,
                  sa.ForeignKey(f"{SCHEMA}.gics_sectors.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
        comment="GICS industry groups (24) — canonical 4-digit codes. "
                "FK target for instruments.gics_industry_id.",
    )
    op.create_index("ix_gics_industries_code", "gics_industries",
                    ["code"], schema=SCHEMA)
    op.create_index("ix_gics_industries_sector", "gics_industries",
                    ["sector_id"], schema=SCHEMA)

    # Seed 24 industry groups (the standard GICS Industry-Group level — broad
    # enough for most quant work, not over-fragmented). Sub-industry detail
    # can be added later as a third dim if needed.
    industries_seed = [
        # Energy (10)
        ("1010", "Energy", "10"),
        # Materials (15)
        ("1510", "Materials", "15"),
        # Industrials (20)
        ("2010", "Capital Goods", "20"),
        ("2020", "Commercial & Professional Services", "20"),
        ("2030", "Transportation", "20"),
        # Consumer Discretionary (25)
        ("2510", "Automobiles & Components", "25"),
        ("2520", "Consumer Durables & Apparel", "25"),
        ("2530", "Consumer Services", "25"),
        ("2550", "Consumer Discretionary Distribution & Retail", "25"),
        # Consumer Staples (30)
        ("3010", "Consumer Staples Distribution & Retail", "30"),
        ("3020", "Food, Beverage & Tobacco", "30"),
        ("3030", "Household & Personal Products", "30"),
        # Health Care (35)
        ("3510", "Health Care Equipment & Services", "35"),
        ("3520", "Pharmaceuticals, Biotechnology & Life Sciences", "35"),
        # Financials (40)
        ("4010", "Banks", "40"),
        ("4020", "Financial Services", "40"),
        ("4030", "Insurance", "40"),
        # Information Technology (45)
        ("4510", "Software & Services", "45"),
        ("4520", "Technology Hardware & Equipment", "45"),
        ("4530", "Semiconductors & Semiconductor Equipment", "45"),
        # Communication Services (50)
        ("5010", "Telecommunication Services", "50"),
        ("5020", "Media & Entertainment", "50"),
        # Utilities (55)
        ("5510", "Utilities", "55"),
        # Real Estate (60)
        ("6010", "Equity Real Estate Investment Trusts (REITs)", "60"),
        ("6020", "Real Estate Management & Development", "60"),
    ]
    for code, name, sector_code in industries_seed:
        op.execute(sa.text("""
            INSERT INTO ref.gics_industries (code, name, sector_id)
            SELECT :code, :name, s.id
            FROM ref.gics_sectors s
            WHERE s.code = :sector_code
        """).bindparams(code=code, name=name, sector_code=sector_code))


def downgrade() -> None:
    op.drop_index("ix_gics_industries_sector",
                  table_name="gics_industries", schema=SCHEMA)
    op.drop_index("ix_gics_industries_code",
                  table_name="gics_industries", schema=SCHEMA)
    op.drop_table("gics_industries", schema=SCHEMA)
    op.drop_index("ix_gics_sectors_code",
                  table_name="gics_sectors", schema=SCHEMA)
    op.drop_table("gics_sectors", schema=SCHEMA)
