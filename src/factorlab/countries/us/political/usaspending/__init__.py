"""USASpending.gov direct ingestion → gov_contracts (sovereign primary path)."""

from factorlab.countries.us.political.usaspending.ingest import ingest_usaspending  # noqa: F401

__all__ = ["ingest_usaspending"]
