"""Provider packages this component ships (US listings, universe membership, bars and EDGAR fundamentals).

The engine loads only these (``factorlab.ingest.registry.load_providers``). A provider
missing here cannot run in this image; each provider belongs to exactly one component
(tests/architecture/test_component_providers.py).
"""

PROVIDERS: tuple[str, ...] = ("schwab", "eodhd", "github_csv", "edgar")
