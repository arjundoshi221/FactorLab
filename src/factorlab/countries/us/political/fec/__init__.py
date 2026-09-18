"""FEC OpenAPI ingestion → fec_committees + campaign_donations.

STUB module — full implementation deferred until FEC_API_KEY is provisioned.
DEMO_KEY caps at 30 req/hr and full backfill needs ~500K-1.5M rows of
filtered schedule_a (PAC + ≥$1K individuals).

Scaffold present so the orchestrator can wire it up; calling `ingest_fec`
without a real key returns a no-op IngestResult with `skipped_no_key=1`.

Sign up at https://api.open.fec.gov/developers
"""

from factorlab.countries.us.political.fec.ingest import ingest_fec  # noqa: F401

__all__ = ["ingest_fec"]
