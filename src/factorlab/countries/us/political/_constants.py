"""Single source of truth for source strings + cross-source constants.

Replaces magic-string literals scattered across ingest modules and scripts.
Use these everywhere — typos in source strings silently cause query misses.
"""

from __future__ import annotations

from typing import Final


class Source:
    """Canonical `legislator_trades.source` / `gov_contracts.source` values.

    These map 1:1 to rows the corresponding ingest module writes. NEVER
    duplicate a string literal — import from here.
    """
    HOUSE_CLERK_PTR: Final = "house_clerk_ptr"
    SENATE_EFD_PTR: Final = "senate_efd_ptr"
    SENATE_STOCK_WATCHER: Final = "senate_stock_watcher_historical"
    USASPENDING: Final = "usaspending_direct"
    FINNHUB_USA_SPENDING: Final = "finnhub_usa_spending"
    LDA: Final = "lda"
    LEGISLATORS_YAML: Final = "legislators_yaml"
    FEC: Final = "fec"
    CONGRESS_GOV: Final = "congress_gov"


# chamber-by-source — used by refresh_bioguide.py and the daily orchestrator
# to know which legislators a given source can target
CHAMBER_BY_SOURCE: Final[dict[str, str]] = {
    Source.HOUSE_CLERK_PTR: "house",
    Source.SENATE_EFD_PTR: "senate",
    Source.SENATE_STOCK_WATCHER: "senate",
}


# UNIQUE-constraint columns on legislator_trades. Migration 023 swapped the
# freeform `source` String for a proper `endpoint_id` Int FK to ref.data_endpoints,
# so every UPSERT must pass these EXACT keys.
LEGISLATOR_TRADES_CONFLICT_KEYS: Final[tuple[str, ...]] = (
    "endpoint_id",
    "country_code",
    "chamber",
    "filing_id",
    "transaction_date",
    "asset_name_raw",
    "transaction_type",
    "amount_str",
)


# Columns whose values get refreshed on UPSERT conflict. Anything not in this
# list keeps its original-insert value — useful for stable fields like
# created_at, asset_type_code (NULL beats wrong, never overwrite a real value
# with NULL on a re-run).
LEGISLATOR_TRADES_UPDATE_COLS: Final[tuple[str, ...]] = (
    "bioguide_id",
    "legislator_name_raw",
    "filing_url",
    "notification_date",
    "filer_type",
    "asset_type_code",
    "ticker",
    "amount_min",
    "amount_max",
    "amount_mid",
    "raw_archive_id",
    "as_of_time",
)
