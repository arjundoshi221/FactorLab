"""Check the v2 raw archive storage policy during application startup."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.storage.clickhouse import ClickHouseStorage


def main() -> None:
    storage = ClickHouseStorage.from_environment()
    result = storage.client.query(
        "SELECT storage_policy FROM system.tables "
        "WHERE database = 'raw' AND name = 'archive'"
    )
    if result.result_rows != [('raw_archive',)]:
        raise RuntimeError("raw.archive must use the raw_archive storage policy")
    print("raw.archive uses the raw_archive storage policy.")


if __name__ == "__main__":
    main()
