"""Resolver registry and construction."""

from __future__ import annotations

from factorlab.components.ingest_us.legacy.eodhd.client import EODHDClient
from factorlab.components.ingest_us.legacy.schwab.market import MarketClient
from factorlab.components.ingest_us.legacy.universe.eodhd import EodhdUniverseResolver
from factorlab.components.ingest_us.legacy.universe.github_csv import GithubCsvUniverseResolver
from factorlab.components.ingest_us.legacy.universe.schwab import SchwabEquityValidator

RESOLVERS = {
    "github_csv": GithubCsvUniverseResolver,
    "eodhd": EodhdUniverseResolver,
}


def create_resolver(config, storage, *, schwab_client=None, eodhd_client=None, session=None):
    try:
        resolver_type = RESOLVERS[config.provider]
    except KeyError as exc:
        raise ValueError(f"unknown universe provider: {config.provider}") from exc
    mapping = getattr(config.providers, config.provider, None)
    if mapping is None:
        raise ValueError(f"provider mapping is missing for {config.provider}")
    validator = SchwabEquityValidator(schwab_client or MarketClient(storage))
    common = {"indexes": mapping.indexes, "validator": validator}
    if config.provider == "github_csv":
        return resolver_type(**common, storage=storage, session=session)
    return resolver_type(
        **common, client=eodhd_client or EODHDClient(storage=storage)
    )

