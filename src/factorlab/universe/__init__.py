"""Stable provider-neutral universe resolution interface."""

from factorlab.universe.base import UniverseResolver
from factorlab.universe.config import UniverseConfig, load_config
from factorlab.universe.eodhd import EodhdUniverseResolver
from factorlab.universe.factory import RESOLVERS, create_resolver
from factorlab.universe.github_csv import GithubCsvUniverseResolver
from factorlab.universe.models import (
    IndexRequest,
    ResolvedConstituent,
    ResolvedUniverse,
    UniverseRequest,
    normalize_symbol,
)
from factorlab.universe.schwab import SchwabEquityValidator

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
