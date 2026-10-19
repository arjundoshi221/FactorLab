"""Refresh India reference datasets through the engine without changing active series."""

import argparse

from factorlab.components.ingest_india.collector import IndiaSink, Sources, refresh
from factorlab.core.logging import configure_logging
from factorlab.storage.v2_india import V2IndiaStorage


def main(argv: list[str] | None = None) -> int:
    configure_logging(component="ingest-india", service="ingest-india-premarket")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", action="append", default=["NSE"])
    args = parser.parse_args(argv)
    sources = Sources()
    sink = IndiaSink(V2IndiaStorage.from_environment())
    try:
        sink.storage.seed_india_reference_data()
        refresh(sink, sources, universe="reference", exchanges=args.exchange, activate=False)
        return 0
    finally:
        sources.close()
        sink.close()
