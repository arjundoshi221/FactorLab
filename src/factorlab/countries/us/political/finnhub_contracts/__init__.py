"""Finnhub /stock/usa-spending → gov_contracts (third-party parallel path).

Finnhub gives us pre-resolved parent-corp → ticker mapping; we use it as a
parallel source to USASpending direct. Both can coexist for the same logical
award (different `source` tag, different `contract_id` namespacing).
"""

from factorlab.countries.us.political.finnhub_contracts.ingest import ingest_finnhub_contracts  # noqa: F401

__all__ = ["ingest_finnhub_contracts"]
