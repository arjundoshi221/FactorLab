"""Create unified audit schema with audit.raw_archive table.

Replaces the two scattered raw-archive tables (`market.raw_responses` and
`alt_political.raw_archive`) with a single auditable home. Every ingest module
will write here going forward — one schema, one table, one FK chain to vendor.

Also seeds vendor-level catch-all endpoint codes that match the legacy
`source` strings present in the existing 19,377 alt_political.raw_archive rows.
This makes migration 016 a clean 1:1 JOIN (no fallback heuristics).

Revision ID: 015
Revises: 014
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "audit"


# Legacy `source` strings present in alt_political.raw_archive that need
# a matching endpoint code so migration 016 can JOIN cleanly.
# Each is added as a vendor-level catch-all under the corresponding vendor.
LEGACY_CATCHALL_ENDPOINTS = [
    # (vendor_code, endpoint_code, name, notes)
    ("house_clerk", "house_clerk",
     "House Clerk vendor-level catch-all (legacy)",
     "Pre-redesign raw_archive rows tagged source='house_clerk' (15,206 rows). "
     "Future writes use granular endpoints (house_clerk_ptr, house_clerk_year_index)."),
    ("fec", "fec",
     "FEC vendor-level catch-all (legacy)",
     "Pre-redesign raw_archive rows tagged source='fec' (3,786 rows)."),
    ("congress_gov", "congress_gov",
     "Congress.gov vendor-level catch-all (legacy)",
     "Pre-redesign raw_archive rows tagged source='congress_gov' (332 rows)."),
    ("usaspending", "usaspending",
     "USAspending vendor-level catch-all (legacy)",
     "Pre-redesign raw_archive rows tagged source='usaspending' (14 rows)."),
    ("senate_stock_watcher", "senate_stock_watcher",
     "SSW vendor-level catch-all (legacy)",
     "Pre-redesign raw_archive rows tagged source='senate_stock_watcher' (6 rows)."),
    ("finnhub", "finnhub",
     "Finnhub vendor-level catch-all (legacy)",
     "Pre-redesign raw_archive rows tagged source='finnhub' (5 rows)."),
    ("lda", "lda",
     "LDA vendor-level catch-all (legacy)",
     "Pre-redesign raw_archive rows tagged source='lda' (2 rows)."),
]


def upgrade() -> None:
    # ── Schema ──────────────────────────────────────────────────────────────
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ── Catch-all endpoints (so migration 016 can JOIN by code) ─────────────
    for vendor_code, ep_code, ep_name, notes in LEGACY_CATCHALL_ENDPOINTS:
        op.execute(sa.text("""
            INSERT INTO ref.data_endpoints (vendor_id, code, name, endpoint_type, notes)
            SELECT v.id, :code, :name, 'rest', :notes
            FROM ref.vendors v
            WHERE v.code = :vcode
              AND NOT EXISTS (
                  SELECT 1 FROM ref.data_endpoints e WHERE e.code = :code
              )
        """).bindparams(vcode=vendor_code, code=ep_code, name=ep_name, notes=notes))

    # ── audit.raw_archive — unified raw payload audit ───────────────────────
    op.create_table(
        "raw_archive",
        sa.Column("raw_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("endpoint_id", sa.Integer,
                  sa.ForeignKey("ref.data_endpoints.id"), nullable=False,
                  comment="Which feed this fetch came from. JOIN to ref.vendors via FK chain."),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False,
                  comment="When the fetch happened."),
        sa.Column("source_url", sa.String(800), nullable=True,
                  comment="Full request URL (already SECRET-scrubbed at write time)."),
        sa.Column("status_code", sa.SmallInteger, nullable=True,
                  comment="HTTP status; NULL for non-HTTP fetches (file reads, scrapes)."),
        sa.Column("content_type", sa.String(80), nullable=True),
        sa.Column("body_bytes", sa.Integer, nullable=True,
                  comment="Decompressed body size in bytes."),
        sa.Column("response_headers", JSONB, nullable=True,
                  comment="Filtered HTTP response headers (no Set-Cookie etc.)."),
        sa.Column("response_bytes", sa.LargeBinary, nullable=True,
                  comment="Raw response body — only when configured to retain (rare)."),
        sa.Column("payload", JSONB, nullable=True,
                  comment="Structured response (preferred over raw bytes for JSON/XML APIs)."),
        sa.Column("instrument_key", sa.String(100), nullable=True,
                  comment="Optional: the instrument this fetch was for (market data)."),
        sa.Column("row_count", sa.Integer, nullable=True,
                  comment="Optional: number of rows extracted from this response."),
        sa.Column("parsed_into", sa.String(80), nullable=True,
                  comment="Optional: target table name (e.g. 'legislator_trades')."),
        sa.Column("metadata_json", JSONB, nullable=True,
                  comment="Free-form per-source metadata (cache hit, retry count, etc.)."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema=SCHEMA,
        comment="Unified raw-payload audit. Replaces alt_political.raw_archive + "
                "market.raw_responses. Every ingest writes here.",
    )

    # Indexes — common access patterns
    op.create_index("ix_raw_archive_endpoint", "raw_archive",
                    ["endpoint_id"], schema=SCHEMA)
    op.create_index("ix_raw_archive_fetched", "raw_archive",
                    ["fetched_at"], schema=SCHEMA)
    op.create_index("ix_raw_archive_parsed_into", "raw_archive",
                    ["parsed_into"], schema=SCHEMA,
                    postgresql_where=sa.text("parsed_into IS NOT NULL"))
    op.create_index("ix_raw_archive_instrument", "raw_archive",
                    ["instrument_key"], schema=SCHEMA,
                    postgresql_where=sa.text("instrument_key IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_raw_archive_instrument", table_name="raw_archive", schema=SCHEMA)
    op.drop_index("ix_raw_archive_parsed_into", table_name="raw_archive", schema=SCHEMA)
    op.drop_index("ix_raw_archive_fetched", table_name="raw_archive", schema=SCHEMA)
    op.drop_index("ix_raw_archive_endpoint", table_name="raw_archive", schema=SCHEMA)
    op.drop_table("raw_archive", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")

    # Remove the catch-all endpoints we added (idempotent — only delete ours)
    for _, ep_code, _, _ in LEGACY_CATCHALL_ENDPOINTS:
        op.execute(sa.text(
            "DELETE FROM ref.data_endpoints WHERE code = :code"
        ).bindparams(code=ep_code))
