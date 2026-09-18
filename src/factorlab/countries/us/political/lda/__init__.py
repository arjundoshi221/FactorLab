"""Senate LDA REST API ingestion (lobbying disclosures).

Endpoints:
  https://lda.senate.gov/api/v1/filings/         paginated quarterly filings
  https://lda.senate.gov/api/v1/clients/         (134K+ clients, used for ticker resolution)
  https://lda.senate.gov/api/v1/constants/...    issue codes, gov entities (already seeded)
"""

from factorlab.countries.us.political.lda.ingest import ingest_lda  # noqa: F401

__all__ = ["ingest_lda"]
