"""Provider packages this component ships (NSE listings, stock-futures contracts and 1-min bars).

The engine loads only these (``factorlab.ingest.registry.load_providers``). A provider
missing here cannot run in this image; each provider belongs to exactly one component
(``tools/components.py check``).
"""

PROVIDERS: tuple[str, ...] = ("upstox",)
