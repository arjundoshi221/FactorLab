"""Cross-source ingestion plumbing.

Submodules:
    security    — redact_url / redact_error / assert_under_root / validate_id_segment
    provider    — v2 provider contract: RawCapture / Provenance / ingestion_run
                  / Provider protocol / run_provider
    errors, registry, bindings, engine, datasets, ...
                — the provider abstraction (docs/architecture/07); import them
                  from their submodules.
"""

from factorlab.ingest.provider import (
    NullProviderStorage,
    Provenance,
    Provider,
    ProviderStorage,
    RawCapture,
    RunContext,
    RunSummary,
    UnitOutcome,
    ingestion_run,
    run_provider,
)
from factorlab.ingest.security import (
    SECRET_QS_KEYS,
    assert_under_root,
    redact_error,
    redact_url,
    validate_id_segment,
    validate_year_segment,
)

__all__ = [
    # security
    "SECRET_QS_KEYS",
    "NullProviderStorage",
    "Provenance",
    "Provider",
    "ProviderStorage",
    # provider
    "RawCapture",
    "RunContext",
    "RunSummary",
    "UnitOutcome",
    "assert_under_root",
    "ingestion_run",
    "redact_error",
    "redact_url",
    "run_provider",
    "validate_id_segment",
    "validate_year_segment",
]
