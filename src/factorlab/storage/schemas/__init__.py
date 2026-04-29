"""SQLAlchemy table definitions — one module per Postgres schema.

Import order matters: ref first (other schemas FK into it), then the rest.
All tables register on the shared metadata in storage.db.
Only import schemas that have table definitions. Stub schemas (alt_social,
alt_political, alt_research, derived, experiments) get added here when built.
"""

from factorlab.storage.schemas import (  # noqa: F401
    ref,
    market,
    universe,
    alt_political,
)
