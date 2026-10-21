"""iShares: ETF holdings files as a membership proxy (IWV for the Russell 3000) and GICS sectors.

Serves ``ref.universe_membership`` and ``ref.security_sectors`` (USA). Registered with
the ingestion engine on import; bound in configs/ingestion/bindings.yaml.
"""

from factorlab.ingest.registry import register_source
from factorlab.sources.ishares.sources import IsharesSectors, IsharesUniverse

register_source(IsharesUniverse)
register_source(IsharesSectors)

__all__ = ["IsharesSectors", "IsharesUniverse"]
