"""Add legislator_trades_dedup view — single row per logical trade across sources.

Multiple endpoints land in alt_political_us.legislator_trades with different
endpoint_id values, so the table-level UNIQUE key cannot collapse them. In
particular, senate_stock_watcher_historical (2012-2020) and senate_efd_ptr
(2020-) cover overlapping windows: every ticker'd eFD row from 2020 onward
has a near-mirror in SSW, producing logical duplicates that inflate
SUM/COUNT queries by ~50% on the senate side for overlapping years.

This view dedupes by source-precedence over the trade fingerprint
(bioguide_id, transaction_date, UPPER(ticker), transaction_type,
filer_type, amount_min, amount_max). Rows missing bioguide_id, ticker,
or transaction_type (paper-PTR placeholders, unresolved bioguide, complex
assets without a ticker) pass through unchanged --- they cannot be safely
matched across sources, so we keep them all.

filer_type and amount_{min,max} are part of the fingerprint to avoid
collapsing legitimate multi-trade days. A representative buying the same
ticker twice on the same day at different amount bands (or one self / one
spouse) is two real trades, not a duplicate. NULLs are treated as
equal to other NULLs by PARTITION BY, which is fine: if both sources lack
the amount/filer breakdown they're still indistinguishable trades.

Source precedence:
    1. senate_efd_ptr               -- direct from efdsearch.senate.gov
    2. senate_efd_paper_llm         -- OCR'd paper PTRs (same source, OCR risk)
    3. senate_stock_watcher_historical  -- third-party mirror
    9. anything else                -- house_clerk_ptr (no overlap), future sources

House data flows through unchanged because house_clerk_ptr is the only house
endpoint --- ROW_NUMBER always = 1 within its partitions.

Adds a `source_code` column so downstream code can see provenance without
joining ref.data_endpoints.

Revision ID: 028
Revises: 027
Create Date: 2026-05-03
"""

from typing import Sequence, Union

from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "alt_political_us"
VIEW = "legislator_trades_dedup"


def upgrade() -> None:
    op.execute(f"""
        CREATE OR REPLACE VIEW {SCHEMA}.{VIEW} AS
        WITH ranked AS (
            SELECT
                t.*,
                e.code AS source_code,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        CASE
                            WHEN t.bioguide_id IS NULL
                              OR t.ticker IS NULL
                              OR t.transaction_type IS NULL
                              THEN t.trade_id::text  -- unique => no dedup
                            ELSE t.bioguide_id
                                 || '|' || t.transaction_date::text
                                 || '|' || UPPER(t.ticker)
                                 || '|' || t.transaction_type
                                 || '|' || COALESCE(t.filer_type, '')
                                 || '|' || COALESCE(t.amount_min::text, '')
                                 || '|' || COALESCE(t.amount_max::text, '')
                        END
                    ORDER BY
                        CASE e.code
                            WHEN 'senate_efd_ptr'                  THEN 1
                            WHEN 'senate_efd_paper_llm'            THEN 2
                            WHEN 'senate_stock_watcher_historical' THEN 3
                            ELSE 9
                        END,
                        -- prefer richer rows on ties
                        CASE WHEN t.amount_min IS NOT NULL THEN 0 ELSE 1 END,
                        CASE WHEN t.filing_url IS NOT NULL THEN 0 ELSE 1 END,
                        t.as_of_time DESC
                ) AS rk
            FROM {SCHEMA}.legislator_trades t
            JOIN ref.data_endpoints e ON e.id = t.endpoint_id
        )
        SELECT
            trade_id, country_code, chamber, bioguide_id, legislator_name_raw,
            filing_id, filing_date, filing_url, transaction_date,
            notification_date, filer_type, transaction_type, asset_name_raw,
            asset_type_code, ticker, security_id, amount_str, amount_min,
            amount_max, amount_mid, raw_archive_id, ingested_at, as_of_time,
            endpoint_id, source_code
        FROM ranked
        WHERE rk = 1
    """)

    op.execute(f"COMMENT ON VIEW {SCHEMA}.{VIEW} IS "
               "'Source-deduped legislator_trades. Partition: "
               "(bioguide_id, transaction_date, UPPER(ticker), transaction_type, "
               "filer_type, amount_min, amount_max). Precedence: senate_efd_ptr > "
               "senate_efd_paper_llm > senate_stock_watcher_historical. "
               "Rows with NULL bioguide / ticker / transaction_type pass through.'")


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{VIEW}")
