"""Relax NOT NULL on legislator_trades.transaction_type + filer_type.

Strict-NULL policy: NULL beats wrong. If the parser cannot determine the
transaction type or filer type from the source PDF/HTML, it should write NULL
rather than fabricate a default ('purchase' / 'self'). The CHECK constraints
are widened to permit NULL alongside the enumerated valid values.

Existing 63K rows are unaffected (all currently have non-NULL values from the
old fabricating-default behavior). Going forward, senate_efd + SSW will write
NULL for unknowns. House Clerk parser already does the right thing.

Revision ID: 027
Revises: 026
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "alt_political_us"


def upgrade() -> None:
    # Drop NOT NULL — pre-existing rows already populated, no parity concern
    op.alter_column("legislator_trades", "transaction_type",
                    existing_type=sa.String(20), nullable=True, schema=SCHEMA)
    op.alter_column("legislator_trades", "filer_type",
                    existing_type=sa.String(20), nullable=True, schema=SCHEMA)

    # Widen CHECK constraints to allow NULL. Postgres CHECK with `IN (...)`
    # rejects NULL by default in some legacy setups; safer to recreate
    # explicitly with `... IS NULL OR ...`.
    op.drop_constraint("ck_legislator_trades_legislator_trades_tx_type_valid",
                       "legislator_trades", schema=SCHEMA)
    op.create_check_constraint(
        "legislator_trades_tx_type_valid",
        "legislator_trades",
        "transaction_type IS NULL OR transaction_type IN "
        "('purchase','sale_full','sale_partial','exchange')",
        schema=SCHEMA,
    )

    op.drop_constraint("ck_legislator_trades_legislator_trades_filer_type_valid",
                       "legislator_trades", schema=SCHEMA)
    op.create_check_constraint(
        "legislator_trades_filer_type_valid",
        "legislator_trades",
        "filer_type IS NULL OR filer_type IN "
        "('self','spouse','dependent_child','joint','junior')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Restore strict NOT NULL — only safe if no row has a NULL in either column.
    n_null = op.get_bind().execute(sa.text(f"""
        SELECT count(*) FROM {SCHEMA}.legislator_trades
        WHERE transaction_type IS NULL OR filer_type IS NULL
    """)).scalar()
    if n_null:
        raise RuntimeError(
            f"cannot downgrade 027: {n_null} rows have NULL transaction_type "
            f"or filer_type. Backfill them first before downgrading."
        )

    op.drop_constraint("ck_legislator_trades_legislator_trades_filer_type_valid",
                       "legislator_trades", schema=SCHEMA)
    op.create_check_constraint(
        "legislator_trades_filer_type_valid",
        "legislator_trades",
        "filer_type IN ('self','spouse','dependent_child','joint','junior')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_legislator_trades_legislator_trades_tx_type_valid",
                       "legislator_trades", schema=SCHEMA)
    op.create_check_constraint(
        "legislator_trades_tx_type_valid",
        "legislator_trades",
        "transaction_type IN ('purchase','sale_full','sale_partial','exchange')",
        schema=SCHEMA,
    )

    op.alter_column("legislator_trades", "filer_type",
                    existing_type=sa.String(20), nullable=False, schema=SCHEMA)
    op.alter_column("legislator_trades", "transaction_type",
                    existing_type=sa.String(20), nullable=False, schema=SCHEMA)
