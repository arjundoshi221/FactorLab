"""House Clerk provider adapter (STOCK Act filing index and PTR transactions).

Importing this package registers its sources. ``parse`` holds the pure parsers
shared with the legacy bootstrap fetchers in ``sources.political.house_clerk``.
"""

from factorlab.ingest.registry import register_source
from factorlab.sources.house_clerk.sources import HouseClerkFilings, HouseClerkTrades

for _source in (HouseClerkFilings, HouseClerkTrades):
    register_source(_source)

__all__ = ["HouseClerkFilings", "HouseClerkTrades"]
