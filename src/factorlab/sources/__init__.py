"""Provider adapters (docs/architecture/07 §3.2, §9.1).

Each package here implements dataset contracts from
``factorlab.shared.ingest.datasets`` and registers its sources on import.
``PROVIDERS`` is the only discovery mechanism: a provider that is not listed
cannot be loaded by :func:`factorlab.shared.ingest.registry.load_providers`.
Packages are not imported here, so ``import factorlab.sources.ibkr`` stays cheap.
"""

PROVIDERS: tuple[str, ...] = (
    "upstox", "eodhd", "schwab", "github_csv", "ibkr", "congress_legislators", "house_clerk",
    "edgar",
)
