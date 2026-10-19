"""Upstox dataset sources used by the production India collector."""

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
