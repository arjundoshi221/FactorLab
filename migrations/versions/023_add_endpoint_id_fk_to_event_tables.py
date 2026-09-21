"""Replace freeform `source` String with `endpoint_id` FK on event tables.

Affected tables (have a `source` String column tagging which feed produced the row):
  alt_political_us.legislator_trades   63,321 rows  (3 distinct sources)
  alt_political_us.gov_contracts        4,914 rows  (2 distinct sources)

Pre-flight verified (2026-05-01): every distinct `source` value resolves to a
`ref.data_endpoints.code` row.

NOT touched (`source` column has different semantics — curation method, not feed):
  alt_political_us.contract_aliases    `source='manual'`  → leave as-is
  alt_political_us.lobby_client_aliases `source='learned'` → leave as-is

Per-table operation:
  1. ADD COLUMN endpoint_id INTEGER (nullable initially)
  2. UPDATE backfill: endpoint_id = ref.data_endpoints.id WHERE code = source
  3. Verify 0 NULL endpoint_id rows (parity gate)
  4. ALTER COLUMN endpoint_id SET NOT NULL + add FK constraint
  5. (legislator_trades only) drop the old uq_legislator_trades_dedup constraint
     and recreate with endpoint_id replacing source
  6. DROP COLUMN source

Code coordination: src/factorlab/sources/political/_constants.py +
_trades.py + every per-source ingest module write `source` strings today.
After this migration runs, those callers MUST switch to writing `endpoint_id`.
The political pipeline is NOT currently scheduled (no live cron writes), so
the migration + code update is safe to do in one session.

Revision ID: 023
Revises: 022
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "alt_political_us"


def _swap_source_to_endpoint_id(
    conn,
    table_name: str,
    *,
    has_unique_constraint: bool = False,
    unique_constraint_name: str | None = None,
    unique_cols_sans_source: tuple[str, ...] | None = None,
) -> None:
    fq = f'"{SCHEMA}"."{table_name}"'

    # 1. Add nullable endpoint_id
    op.add_column(table_name, sa.Column("endpoint_id", sa.Integer, nullable=True),
                  schema=SCHEMA)

    # 2. Backfill from source
    conn.execute(sa.text(f"""
        UPDATE {fq} t
        SET endpoint_id = e.id
        FROM ref.data_endpoints e
        WHERE e.code = t.source
    """))

    # 3. Parity gate
    n_null = conn.execute(sa.text(
        f"SELECT count(*) FROM {fq} WHERE endpoint_id IS NULL AND source IS NOT NULL"
    )).scalar()
    if n_null:
        # surface the unmapped source values for debugging
        bad = conn.execute(sa.text(
            f"SELECT DISTINCT source FROM {fq} WHERE endpoint_id IS NULL "
            f"AND source IS NOT NULL"
        )).fetchall()
        raise RuntimeError(
            f"backfill FAIL on {SCHEMA}.{table_name}: {n_null} rows have "
            f"endpoint_id NULL after backfill. Unmapped source values: "
            f"{[r[0] for r in bad]}"
        )

    # 4. NOT NULL + FK
    op.alter_column(table_name, "endpoint_id", nullable=False, schema=SCHEMA)
    op.create_foreign_key(
        f"fk_{table_name}_endpoint_id_data_endpoints",
        table_name, "data_endpoints",
        ["endpoint_id"], ["id"],
        source_schema=SCHEMA, referent_schema="ref",
    )
    # Index on endpoint_id (for vendor-by-vendor queries)
    op.create_index(
        f"ix_{table_name}_endpoint_id", table_name, ["endpoint_id"], schema=SCHEMA,
    )

    # 5. Rebuild unique constraint (legislator_trades only)
    if has_unique_constraint:
        op.drop_constraint(unique_constraint_name, table_name, schema=SCHEMA)
        op.create_unique_constraint(
            unique_constraint_name, table_name,
            ["endpoint_id", *unique_cols_sans_source],
            schema=SCHEMA,
        )

    # 6. Drop the old source column
    op.drop_column(table_name, "source", schema=SCHEMA)


def upgrade() -> None:
    conn = op.get_bind()

    # legislator_trades — has the source-containing unique constraint
    _swap_source_to_endpoint_id(
        conn, "legislator_trades",
        has_unique_constraint=True,
        unique_constraint_name="uq_legislator_trades_dedup",
        unique_cols_sans_source=(
            "country_code", "chamber", "filing_id", "transaction_date",
            "asset_name_raw", "transaction_type", "amount_str",
        ),
    )

    # gov_contracts — no source-containing unique constraint
    _swap_source_to_endpoint_id(
        conn, "gov_contracts",
        has_unique_constraint=False,
    )


def downgrade() -> None:
    """Reverse: re-add `source` String column, populate from endpoint_id JOIN, drop endpoint_id."""
    conn = op.get_bind()

    for table_name, uniq_name, uniq_cols in [
        ("legislator_trades", "uq_legislator_trades_dedup",
         ("country_code", "chamber", "filing_id", "transaction_date",
          "asset_name_raw", "transaction_type", "amount_str")),
        ("gov_contracts", None, None),
    ]:
        fq = f'"{SCHEMA}"."{table_name}"'
        op.add_column(table_name, sa.Column("source", sa.String(50), nullable=True),
                      schema=SCHEMA)
        conn.execute(sa.text(f"""
            UPDATE {fq} t SET source = e.code
            FROM ref.data_endpoints e WHERE e.id = t.endpoint_id
        """))
        op.alter_column(table_name, "source", nullable=False, schema=SCHEMA)

        if uniq_name:
            op.drop_constraint(uniq_name, table_name, schema=SCHEMA)
            op.create_unique_constraint(uniq_name, table_name,
                                        ["source", *uniq_cols], schema=SCHEMA)

        op.drop_index(f"ix_{table_name}_endpoint_id", table_name=table_name, schema=SCHEMA)
        op.drop_constraint(f"fk_{table_name}_endpoint_id_data_endpoints",
                           table_name, schema=SCHEMA)
        op.drop_column(table_name, "endpoint_id", schema=SCHEMA)
