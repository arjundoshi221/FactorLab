"""congress-legislators provider adapter (current legislators, committees, memberships).

Importing this package registers its source.
"""

from factorlab.ingest.registry import register_source
from factorlab.sources.congress_legislators.sources import CongressLegislators

register_source(CongressLegislators)

__all__ = ["CongressLegislators"]
