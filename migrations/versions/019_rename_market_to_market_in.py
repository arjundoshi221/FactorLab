"""Rename market schema -> market_in for per-country separation.

Currently `market` holds India-only data (529,035 rows in candles_1min from
Upstox/IBKR, plus empty candles_5min and candles_daily hypertables). Renamed
to `market_in` so future US prices land in a separate `market_us` schema
(created in migration 020).

Mechanics: ALTER SCHEMA on a schema containing TimescaleDB hypertables.
Verified safe: TS catalog references hypertables by OID, not name, so the
rename is atomic and the hypertable / chunk policy survives.

Verified pre-flight (2026-05-01):
  - 529,035 rows in candles_1min (India), 0 in candles_5min, 0 in candles_daily
  - 4 outbound FKs to ref.instruments + ref.contracts (unaffected by rename)
  - 0 inbound FKs from outside `market` schema
  - 0 views, 0 functions

CAUTION: India 5-min Task Scheduler poller writes to market.candles_1min
every 5 min during market hours. Disable scheduled tasks before applying:
  Disable-ScheduledTask -TaskName FactorLab-IndiaEquities-Upstox-Live, FactorLab-IndiaEquities-Upstox-PreMarket

Re-enable only after Phase D updates the India ingest scripts to target
`market_in.fact_equity` (with freq_id FK).

Revision ID: 019
Revises: 018
Create Date: 2026-05-01
"""

from typing import Sequence, Union

from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER SCHEMA market RENAME TO market_in")


def downgrade() -> None:
    op.execute("ALTER SCHEMA market_in RENAME TO market")
