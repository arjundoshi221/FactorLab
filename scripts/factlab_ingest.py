"""Run the provider-agnostic ingestion engine with every provider (development and ops).

Each ingest component runs the same engine with only the providers it ships;
this entry point loads all of them. See ``factorlab.orchestration.cli`` for usage::

    python scripts/factlab_ingest.py validate
    python scripts/factlab_ingest.py run --dataset ref.listings --market IND --dry-run
"""

from factorlab.components.ingest_broker.providers import PROVIDERS as BROKER
from factorlab.components.ingest_india.providers import PROVIDERS as INDIA
from factorlab.components.ingest_political.providers import PROVIDERS as POLITICAL
from factorlab.components.ingest_us.providers import PROVIDERS as US
from factorlab.orchestration.cli import main

ALL_PROVIDERS = INDIA + US + POLITICAL + BROKER

if __name__ == "__main__":
    raise SystemExit(main(providers=ALL_PROVIDERS, prog="factlab_ingest.py"))
