"""DB-service side of the ingestion abstraction (docs/architecture/07 §7-8).

Sinks accept provider-neutral records, resolve identity, mint canonical UUIDs
and stamp provenance. Nothing here may import a provider or name one
(rules R2/R3, enforced by ``tests/architecture/test_boundaries.py``).
"""

from factorlab.storage.sinks.clickhouse import ClickHouseSinks

__all__ = ["ClickHouseSinks"]
