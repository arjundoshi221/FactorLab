"""Senate Stock Watcher historical (2014-2019, frozen GitHub mirror).

Single JSON download, ~8,350 trades. One-shot.
"""

from factorlab.countries.us.political.senate_stock_watcher.ingest import ingest_senate_stock_watcher  # noqa: F401

__all__ = ["ingest_senate_stock_watcher"]
