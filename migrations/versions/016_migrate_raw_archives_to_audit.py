"""Migrate scattered raw-archive rows into unified audit.raw_archive.

Sources:
  - alt_political.raw_archive — 19,377 rows (live audit). 9 distinct source strings;
    each maps cleanly to an endpoint code (vendor-level catch-alls were seeded in
    migration 015 for legacy strings like 'house_clerk', 'fec', etc.).
  - market.raw_responses — 0 rows currently. Schema-only handling for forward
    compatibility (any rows that DO exist get migrated and the table dropped).

Verification gate: per-source row-count parity before dropping source tables.

Drop targets after parity check passes:
  - alt_political.raw_archive — replaced by audit.raw_archive
  - market.raw_responses — replaced by audit.raw_archive

Note: the `raw_archive_id` columns on alt_political event tables (legislator_trades,
gov_contracts, lobbying_filings, campaign_donations, bills, hearings) are 100%
NULL today (verified pre-migration), so no FK lineage is at risk. Those columns
stay intact (orphan but harmless) and can be repointed at audit.raw_archive in
a future migration if downstream lineage tracking is enabled.

Revision ID: 016
Revises: 015
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Pre-flight: every distinct source string must resolve to an endpoint ─
    unmapped_pol = conn.execute(sa.text("""
        SELECT DISTINCT source FROM alt_political.raw_archive
        WHERE NOT EXISTS (
            SELECT 1 FROM ref.data_endpoints e WHERE e.code = source
        )
    """)).fetchall()
    if unmapped_pol:
        raise RuntimeError(
            f"alt_political.raw_archive has {len(unmapped_pol)} unmapped source "
            f"values (no matching endpoint code): {[r[0] for r in unmapped_pol]}. "
            f"Add catch-all endpoints in migration 015 before retrying."
        )

    unmapped_mkt = conn.execute(sa.text("""
        SELECT DISTINCT source FROM market.raw_responses
        WHERE NOT EXISTS (
            SELECT 1 FROM ref.data_endpoints e WHERE e.code = source
        )
    """)).fetchall()
    if unmapped_mkt:
        raise RuntimeError(
            f"market.raw_responses has unmapped source values: "
            f"{[r[0] for r in unmapped_mkt]}"
        )

    # Capture pre-move counts for parity verification
    n_pol_pre = conn.execute(sa.text(
        "SELECT count(*) FROM alt_political.raw_archive"
    )).scalar()
    n_mkt_pre = conn.execute(sa.text(
        "SELECT count(*) FROM market.raw_responses"
    )).scalar()
    n_audit_pre = conn.execute(sa.text(
        "SELECT count(*) FROM audit.raw_archive"
    )).scalar()  # should be 0; just-created in migration 015

    # ── Move alt_political.raw_archive → audit.raw_archive ──────────────────
    # Preserve raw_id (UUID PK); resolve source string → endpoint_id via JOIN.
    op.execute(sa.text("""
        INSERT INTO audit.raw_archive (
            raw_id, endpoint_id, fetched_at, source_url, status_code,
            response_headers, response_bytes, metadata_json
        )
        SELECT
            ra.raw_id,
            e.id,
            ra.fetched_at,
            ra.source_url,
            ra.status_code,
            ra.response_headers,
            ra.response_bytes,
            ra.metadata_json
        FROM alt_political.raw_archive ra
        JOIN ref.data_endpoints e ON e.code = ra.source
    """))

    # ── Move market.raw_responses → audit.raw_archive ───────────────────────
    # Different source schema:
    #   raw_responses.id            → audit.raw_archive.raw_id
    #   raw_responses.endpoint      → source_url (it's the URL path)
    #   raw_responses.payload       → audit.raw_archive.payload
    #   raw_responses.instrument_key, row_count, parsed_into → same names
    # No status_code, response_headers, response_bytes, metadata_json — all NULL.
    op.execute(sa.text("""
        INSERT INTO audit.raw_archive (
            raw_id, endpoint_id, fetched_at, source_url,
            payload, instrument_key, row_count, parsed_into
        )
        SELECT
            rr.id,
            e.id,
            rr.fetched_at,
            rr.endpoint,
            rr.payload,
            rr.instrument_key,
            rr.row_count,
            rr.parsed_into
        FROM market.raw_responses rr
        JOIN ref.data_endpoints e ON e.code = rr.source
    """))

    # ── Parity verification ─────────────────────────────────────────────────
    n_audit_post = conn.execute(sa.text(
        "SELECT count(*) FROM audit.raw_archive"
    )).scalar()
    expected = n_audit_pre + n_pol_pre + n_mkt_pre

    if n_audit_post != expected:
        raise RuntimeError(
            f"Row-count parity FAIL: audit pre={n_audit_pre}, "
            f"alt_political moved={n_pol_pre}, market moved={n_mkt_pre}, "
            f"expected total={expected}, got={n_audit_post}. "
            f"Refusing to drop source tables."
        )

    # ── Drop source tables ──────────────────────────────────────────────────
    op.drop_table("raw_archive", schema="alt_political")
    op.drop_table("raw_responses", schema="market")


def downgrade() -> None:
    """Best-effort reverse: recreate source tables (empty) and split rows back.

    Note: this loses the unified raw_id space if any rows were inserted into
    audit.raw_archive AFTER 016 ran but BEFORE this downgrade — those new rows
    can't be classified back into one of the two old tables. We split by
    looking up vendor name on the endpoint chain.
    """
    # Recreate alt_political.raw_archive (matching original 007 schema)
    op.create_table(
        "raw_archive",
        sa.Column("raw_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(800), nullable=False),
        sa.Column("response_bytes", sa.LargeBinary, nullable=True),
        sa.Column("response_headers", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status_code", sa.SmallInteger, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.dialects.postgresql.JSONB, nullable=True),
        schema="alt_political",
    )
    # Recreate market.raw_responses (matching original 004 schema)
    op.create_table(
        "raw_responses",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("endpoint", sa.String(200), nullable=False),
        sa.Column("instrument_key", sa.String(100), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("parsed_into", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        schema="market",
    )

    # Split audit.raw_archive rows back. Heuristic: if vendor in ('upstox',
    # 'ibkr','eodhd','schwab') → market.raw_responses; else → alt_political.
    op.execute(sa.text("""
        INSERT INTO market.raw_responses (
            id, fetched_at, source, endpoint, instrument_key,
            payload, row_count, parsed_into
        )
        SELECT a.raw_id, a.fetched_at, v.code, COALESCE(a.source_url, ''),
               a.instrument_key, COALESCE(a.payload, '{}'::jsonb),
               a.row_count, a.parsed_into
        FROM audit.raw_archive a
        JOIN ref.data_endpoints e ON e.id = a.endpoint_id
        JOIN ref.vendors v ON v.id = e.vendor_id
        WHERE v.code IN ('upstox','ibkr','eodhd','schwab')
    """))
    op.execute(sa.text("""
        INSERT INTO alt_political.raw_archive (
            raw_id, source, source_url, response_bytes, response_headers,
            status_code, fetched_at, metadata_json
        )
        SELECT a.raw_id, v.code, COALESCE(a.source_url, ''),
               a.response_bytes, a.response_headers, a.status_code,
               a.fetched_at, a.metadata_json
        FROM audit.raw_archive a
        JOIN ref.data_endpoints e ON e.id = a.endpoint_id
        JOIN ref.vendors v ON v.id = e.vendor_id
        WHERE v.code NOT IN ('upstox','ibkr','eodhd','schwab')
    """))
