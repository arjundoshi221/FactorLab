"""Stable provider-neutral universe resolution interface."""

from factorlab.components.ingest_us.legacy.universe.base import UniverseResolver
from factorlab.components.ingest_us.legacy.universe.config import UniverseConfig, load_config
from factorlab.components.ingest_us.legacy.universe.eodhd import EodhdUniverseResolver
from factorlab.components.ingest_us.legacy.universe.factory import RESOLVERS, create_resolver
from factorlab.components.ingest_us.legacy.universe.github_csv import GithubCsvUniverseResolver
from factorlab.components.ingest_us.legacy.universe.models import (
    IndexRequest,
    ResolvedConstituent,
    ResolvedUniverse,
    UniverseRequest,
    normalize_symbol,
)
from factorlab.components.ingest_us.legacy.universe.schwab import SchwabEquityValidator

__all__ = [
    "RESOLVERS",
    "EodhdUniverseResolver",
    "GithubCsvUniverseResolver",
    "IndexRequest",
    "ResolvedConstituent",
    "ResolvedUniverse",
    "SchwabEquityValidator",
    "UniverseConfig",
    "UniverseRequest",
    "UniverseResolver",
    "create_resolver",
    "load_config",
    "normalize_symbol",
]
