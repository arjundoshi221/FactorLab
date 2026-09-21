"""Purge probe / smoke-test traffic from production audit.

Policy: probe / development / smoke-test endpoints don't belong in production
audit. The `lda_probe` endpoint is the only such entry today (2 rows carried
over by migration 016). Action:

  1. DELETE the 2 rows from audit.raw_archive whose endpoint is `lda_probe`.
  2. Mark the `lda_probe` endpoint `active = false` (keep the row for
     historical lineage but disable future writes).

Rationale: keeping the endpoint row but deactivating it preserves the audit
trail (which probe traffic existed and when) without contaminating active
production queries that filter `WHERE active = true`.

Going forward, ingest modules MUST NOT write probe traffic to audit. Probe
runs should either skip the audit write (`PoliticalHTTPClient(write_archive=False)`)
or write to a separate dev/staging DB.

Revision ID: 017
Revises: 016
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Probe / smoke-test endpoint codes that should never appear in prod audit.
PROBE_ENDPOINT_CODES = ["lda_probe"]


def upgrade() -> None:
    conn = op.get_bind()

    # Pre-purge count for logging
    n_before = conn.execute(sa.text("""
        SELECT count(*) FROM audit.raw_archive a
        JOIN ref.data_endpoints e ON e.id = a.endpoint_id
        WHERE e.code = ANY(:codes)
    """), {"codes": PROBE_ENDPOINT_CODES}).scalar()

    # 1. Delete probe rows from audit
    conn.execute(sa.text("""
        DELETE FROM audit.raw_archive
        WHERE endpoint_id IN (
            SELECT id FROM ref.data_endpoints WHERE code = ANY(:codes)
        )
    """), {"codes": PROBE_ENDPOINT_CODES})

    # 2. Deactivate the probe endpoint(s) — keep row for history
    conn.execute(sa.text("""
        UPDATE ref.data_endpoints
        SET active = false
        WHERE code = ANY(:codes)
    """), {"codes": PROBE_ENDPOINT_CODES})

    print(f"[017] purged {n_before} probe rows from audit.raw_archive; "
          f"deactivated {len(PROBE_ENDPOINT_CODES)} probe endpoint(s).")


def downgrade() -> None:
    """Re-activate the probe endpoint(s). The deleted audit rows are NOT
    restored — they were dev-only and the deletion is intentional."""
    op.execute(sa.text("""
        UPDATE ref.data_endpoints SET active = true
        WHERE code = ANY(:codes)
    """).bindparams(codes=PROBE_ENDPOINT_CODES))
