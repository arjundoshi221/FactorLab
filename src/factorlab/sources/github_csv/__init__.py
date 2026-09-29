"""GitHub-hosted CSV index constituents (e.g. the ``datasets/s-and-p-500-companies`` repo).

Serves ``ref.universe_membership`` only. Constituents are bare tickers, so the
DB service resolves them within the country (07 §8.1). Replaces
``universe/github_csv.py`` + Schwab validation at the P4 cutover: validation
becomes resolution against listings the reference providers already own.
"""

from factorlab.ingest.registry import register_source
from factorlab.sources.github_csv.sources import GithubCsvUniverse

register_source(GithubCsvUniverse)

__all__ = ["GithubCsvUniverse"]
