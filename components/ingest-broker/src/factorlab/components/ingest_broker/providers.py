"""Provider packages this component ships (the read-only IBKR account mirror).

The engine loads only these (``factorlab.ingest.registry.load_providers``). A provider
missing here cannot run in this image; each provider belongs to exactly one component
(``tools/components.py check``).
"""

PROVIDERS: tuple[str, ...] = ("ibkr",)
