"""unitedstates/congress-legislators YAML loader → 5 alt_political_us dim tables.

Loads:
  legislators-current.yaml + legislators-historical.yaml  → legislators + legislator_terms + legislator_fec_ids
  committees-current.yaml + committees-historical.yaml    → committees
  committee-membership-current.yaml                       → committee_assignments
"""

from factorlab.countries.us.political.legislators.ingest import ingest_legislators  # noqa: F401

__all__ = ["ingest_legislators"]
