"""Provider packages this component ships (congressional references, House filings and trades).

The engine loads only these (``factorlab.ingest.registry.load_providers``). A provider
missing here cannot run in this image; each provider belongs to exactly one component
(tests/architecture/test_component_providers.py).
"""

PROVIDERS: tuple[str, ...] = ("house_clerk", "congress_legislators")
