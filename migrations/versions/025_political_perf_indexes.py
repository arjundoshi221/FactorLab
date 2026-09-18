"""Add 3 partial indexes flagged by the original architect review for the
flagship `committee × contract × recent purchase` conjunction.

  ix_csm_strength
    ON alt_political_us.committee_sector_map (signal_strength)
    WHERE signal_strength IN ('high','very_high','extreme','medium_high')
    -- The conjunction filters this set; partial = smaller index, faster scan.

  ix_legislator_trades_tx_date_partial
    ON alt_political_us.legislator_trades (transaction_date DESC)
    WHERE transaction_date IS NOT NULL
    -- Existing (bioguide_id, transaction_date) composite covers per-legislator
    -- queries; this transaction_date-only index helps the conjunction's
    -- `WHERE transaction_date >= CURRENT_DATE - INTERVAL '180 days'` CTE.

  ix_gov_contracts_ticker_partial
    ON alt_political_us.gov_contracts (ticker)
    WHERE ticker IS NOT NULL
    -- Existing (ticker, action_date) composite is too wide for DISTINCT ticker
    -- pulls; this slim ticker-only partial speeds the contractor sub-query.

Revision ID: 025
Revises: 024
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY would be ideal but requires running outside a tx — alembic
    # wraps each migration in a tx. Tables are small (committee_sector_map=21,
    # gov_contracts=4914, legislator_trades=63321), so plain CREATE INDEX is fine.
    op.execute("""
        CREATE INDEX ix_csm_strength
        ON alt_political_us.committee_sector_map (signal_strength)
        WHERE signal_strength IN ('high','very_high','extreme','medium_high')
    """)
    op.execute("""
        CREATE INDEX ix_legislator_trades_tx_date_partial
        ON alt_political_us.legislator_trades (transaction_date DESC)
        WHERE transaction_date IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX ix_gov_contracts_ticker_partial
        ON alt_political_us.gov_contracts (ticker)
        WHERE ticker IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS alt_political_us.ix_gov_contracts_ticker_partial")
    op.execute("DROP INDEX IF EXISTS alt_political_us.ix_legislator_trades_tx_date_partial")
    op.execute("DROP INDEX IF EXISTS alt_political_us.ix_csm_strength")
