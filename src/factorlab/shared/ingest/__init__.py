"""Cross-source ingestion plumbing.

Submodules:
    security    — redact_url / redact_error / assert_under_root / validate_id_segment
    state       — per-source resume checkpoints (JSON-backed Set[str])
    http        — HTTPClient: retry + disk cache + audit.raw_archive writer
                  + RateLimitDeferred exception
    backfill    — Backfiller protocol + BackfillPlan / BackfillReport + registry
"""

from factorlab.shared.ingest.backfill import (
    BackfillPlan,
    BackfillReport,
    Backfiller,
    BackfillRegistry,
    register_backfiller,
    get_backfiller,
    list_backfillers,
)
from factorlab.shared.ingest.http import HTTPClient, RateLimitDeferred
from factorlab.shared.ingest.security import (
    SECRET_QS_KEYS,
    assert_under_root,
    redact_error,
    redact_url,
    validate_id_segment,
    validate_year_segment,
)
from factorlab.shared.ingest.state import State

__all__ = [
    # security
    "SECRET_QS_KEYS",
    "redact_url", "redact_error",
    "validate_id_segment", "validate_year_segment",
    "assert_under_root",
    # state
    "State",
    # http
    "HTTPClient", "RateLimitDeferred",
    # backfill
    "Backfiller", "BackfillPlan", "BackfillReport", "BackfillRegistry",
    "register_backfiller", "get_backfiller", "list_backfillers",
]
