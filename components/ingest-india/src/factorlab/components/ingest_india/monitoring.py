"""Interruptible monitoring for the production collector's idle waits."""

from factorlab.runtime.monitor import monitoring_sleep


def sleep(seconds: float) -> None:
    from factorlab.components.ingest_india.legacy import daemon

    monitoring_sleep(seconds, should_stop=lambda: daemon.shutdown.triggered)
