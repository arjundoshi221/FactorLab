"""Check the v2 raw archive storage policy during application startup."""

from __future__ import annotations

import logging

from factorlab.clickhouse import ClickHouse
from factorlab.core.logging import configure_logging

log = logging.getLogger(__name__)


def main() -> None:
    configure_logging(component="schema-migrator", service="bootstrap")
    storage = ClickHouse.from_environment()
    result = storage.client.query(
        "SELECT storage_policy FROM system.tables WHERE database = 'raw' AND name = 'archive'"
    )
    if result.result_rows != [("raw_archive",)]:
        raise RuntimeError("raw.archive must use the raw_archive storage policy")
    log.info("raw.archive uses the raw_archive storage policy.")


if __name__ == "__main__":
    main()
