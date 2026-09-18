"""Congress.gov ingestion → bills + bill_sponsors + bill_committees + bill_actions
+ hearings.

Three-pass design for bills (cost-shaping — see docs/data-sources/political/senator-trades.md §1C):
  Pass A — list pull       (~210 calls / 3 congresses; skeleton rows)
  Pass B — detail enrich   (~52K calls; adds policy_area + introduced_date + primary sponsor)
  Pass C — deep fetch      (~63K calls; cosponsors+committees+actions, priority policy areas only)
Hearings: list+detail (~5K calls).

`hearing_witnesses` table stays empty in v1 — Congress.gov v3 doesn't expose
structured witness lists; transcripts at `formats[].url` would need a separate
parser. Schema preserved for a future extraction pass.
"""

from factorlab.countries.us.political.congress_gov.ingest import (  # noqa: F401
    ingest_bills_deep,
    ingest_bills_detail,
    ingest_bills_list,
    ingest_congress_gov,
    ingest_hearings,
)

__all__ = [
    "ingest_congress_gov",
    "ingest_bills_list",
    "ingest_bills_detail",
    "ingest_bills_deep",
    "ingest_hearings",
]
