"""EODHD dataset sources for configured index membership and optional market feeds."""

from factorlab.ingest.registry import register_source
from factorlab.sources.eodhd.sources import EodhdDailyBars, EodhdListings, EodhdUniverse

for _source in (EodhdListings, EodhdUniverse, EodhdDailyBars):
    register_source(_source)

__all__ = ["EodhdDailyBars", "EodhdListings", "EodhdUniverse"]
