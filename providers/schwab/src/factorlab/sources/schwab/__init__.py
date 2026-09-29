"""Charles Schwab Trader API source.

``sources`` holds the dataset adapters (07 §6); importing this package registers
them. The production US daemon's legacy client lives in
``factorlab.components.ingest_us.legacy.schwab`` until its cutover.
"""

from factorlab.ingest.registry import register_source
from factorlab.sources.schwab.sources import SchwabBars, SchwabListings

for _source in (SchwabListings, SchwabBars):
    register_source(_source)

__all__ = ["SchwabBars", "SchwabListings"]
