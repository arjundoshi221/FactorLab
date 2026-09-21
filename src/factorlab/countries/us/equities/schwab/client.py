"""Schwab authenticated client factory.

Thin wrapper around ``ensure_client`` so callers can do::

    from factorlab.countries.us.equities.schwab import get_client
    client = get_client()
    quote = client.get_quote("AAPL").json()

The returned client is a ``schwab.client.Client`` instance from ``schwab-py``.
It MUST NOT be used for trading endpoints -- see
``docs/data-sources/us/schwab.md`` ("Hard rule: no trading").
"""

import logging

from factorlab.countries.us.equities.schwab.auth import ensure_client

log = logging.getLogger(__name__)


def get_client(interactive: bool = False):
    """Return a ready-to-use ``schwab-py`` client (read-only market data).

    Args:
        interactive: If True, opens a browser for OAuth when no valid token
                     exists. Default False (safe for cron / scripted use).

    See ``factorlab.countries.us.equities.schwab.auth.ensure_client`` for resolution order.
    """
    return ensure_client(interactive=interactive)
