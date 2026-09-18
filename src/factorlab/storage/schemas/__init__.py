"""SQLAlchemy table definitions — one module per Postgres schema.

Import order matters: ref first (other schemas FK into it), then the rest.
All tables register on the shared metadata in storage.db.
Only import schemas that have table definitions. Stub schemas (alt_social,
alt_research, derived, experiments) get added here when built. Per-country
schemas land as they're activated (e.g. alt_political_in, market_us, ...).
"""

from factorlab.storage.schemas import (  # noqa: F401
    ref,
    audit,
    market_in,
    market_us,
    universe,
    alt_political_us,
)
