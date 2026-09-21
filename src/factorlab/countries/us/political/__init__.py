"""US Congress + executive-branch political-data ingestion.

Pulls trades, lobbying, contracts, donations, bills, hearings into the
26-table `alt_political_us` schema. Country-tagged (default 'US').

Public API:
    from factorlab.countries.us.political import (
        ingest_legislators,
        ingest_house_clerk,
        ingest_senate_stock_watcher,
        ingest_senate_efd,
        ingest_lda,
        ingest_usaspending,
        ingest_finnhub_contracts,
    )
"""

from __future__ import annotations

# Re-exports populated as each source ingestion module lands.
__all__ = [
    "ingest_legislators",
    "ingest_house_clerk",
    "ingest_senate_stock_watcher",
    "ingest_senate_efd",
    "ingest_lda",
    "ingest_usaspending",
    "ingest_finnhub_contracts",
]


def __getattr__(name: str):  # lazy imports — avoids loading every source on package import
    if name == "ingest_legislators":
        from factorlab.countries.us.political.legislators.ingest import ingest_legislators
        return ingest_legislators
    if name == "ingest_house_clerk":
        from factorlab.countries.us.political.house_clerk.ingest import ingest_house_clerk
        return ingest_house_clerk
    if name == "ingest_senate_stock_watcher":
        from factorlab.countries.us.political.senate_stock_watcher.ingest import ingest_senate_stock_watcher
        return ingest_senate_stock_watcher
    if name == "ingest_senate_efd":
        from factorlab.countries.us.political.senate_efd.ingest import ingest_senate_efd
        return ingest_senate_efd
    if name == "ingest_lda":
        from factorlab.countries.us.political.lda.ingest import ingest_lda
        return ingest_lda
    if name == "ingest_usaspending":
        from factorlab.countries.us.political.usaspending.ingest import ingest_usaspending
        return ingest_usaspending
    if name == "ingest_finnhub_contracts":
        from factorlab.countries.us.political.finnhub_contracts.ingest import ingest_finnhub_contracts
        return ingest_finnhub_contracts
    raise AttributeError(name)
