"""Place the immutable raw HTTP archive on the production raw-data disk."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.storage.clickhouse import ClickHouseStorage


def main() -> None:
    storage = ClickHouseStorage.from_environment()
    storage.client.command(
        "ALTER TABLE raw_http_archive MODIFY SETTING storage_policy = 'raw_archive'"
    )
    print("raw_http_archive now uses the raw_archive storage policy.")


if __name__ == "__main__":
    main()
