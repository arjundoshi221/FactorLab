"""EODHD provider adapter (US: listed-equity master, index constituents, unadjusted daily bars).

Importing this package registers its sources. The legacy
``factorlab.countries.us.equities.eodhd`` package keeps serving the
production universe job until the P4 cutover (07 §15).
"""

from factorlab.shared.ingest.registry import register_source
from factorlab.sources.eodhd.sources import EodhdDailyBars, EodhdListings, EodhdUniverse

for _source in (EodhdListings, EodhdUniverse, EodhdDailyBars):
    register_source(_source)

__all__ = ["EodhdDailyBars", "EodhdListings", "EodhdUniverse"]
