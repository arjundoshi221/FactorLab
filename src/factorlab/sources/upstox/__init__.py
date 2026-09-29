"""Upstox provider adapter (India: NSE listings, futures contracts, 1min bars).

Importing this package registers its sources; nothing else should import the
modules below from outside ``factorlab.sources.upstox`` (07 rule R4). The
legacy ``factorlab.components.ingest_india.legacy.upstox`` package still serves the
production daemons until the P3 cutover (07 §15).
"""

from factorlab.ingest.registry import register_source
from factorlab.sources.upstox.sources import (
    UpstoxBars,
    UpstoxContractBars,
    UpstoxContracts,
    UpstoxListings,
)

for _source in (UpstoxListings, UpstoxContracts, UpstoxBars, UpstoxContractBars):
    register_source(_source)

__all__ = ["UpstoxBars", "UpstoxContractBars", "UpstoxContracts", "UpstoxListings"]
