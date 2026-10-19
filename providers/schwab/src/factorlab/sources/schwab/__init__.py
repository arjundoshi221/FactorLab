"""Schwab dataset sources used by US reference and price collection."""

from factorlab.ingest.registry import register_source
from factorlab.sources.schwab.sources import SchwabBars, SchwabListings

for _source in (SchwabListings, SchwabBars):
    register_source(_source)

__all__ = ["SchwabBars", "SchwabListings"]
