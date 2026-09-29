"""Charles Schwab Trader API source.

``client`` / ``market`` serve the production US daemon unchanged. ``sources``
holds the dataset adapters (07 §6); importing this package registers them.
"""

from factorlab.shared.ingest.registry import register_source
from factorlab.sources.schwab.client import SchwabSession, get_session
from factorlab.sources.schwab.sources import SchwabBars, SchwabListings

for _source in (SchwabListings, SchwabBars):
    register_source(_source)

__all__ = ["SchwabBars", "SchwabListings", "SchwabSession", "get_session"]
