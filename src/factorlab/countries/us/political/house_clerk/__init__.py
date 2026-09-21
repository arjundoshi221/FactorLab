"""House Clerk PTR ingestion.

Endpoints:
  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}FD.ZIP
    → annual filing index XML (no transactions, just filing metadata)
  https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf
    → per-filing PDF with transaction line items

Flow: list_ptrs() → fetch_pdf() → parse_pdf() → upsert legislator_trades.
"""

from factorlab.countries.us.political.house_clerk.ingest import ingest_house_clerk  # noqa: F401

__all__ = ["ingest_house_clerk"]
