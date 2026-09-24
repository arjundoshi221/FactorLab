"""Wait for a fresh raw-to-curated v2 write after release activation."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.storage.clickhouse import ClickHouseStorage


def _count(client, table: str, since: datetime) -> int:
    return int(client.query(
        f"SELECT count() FROM {table} "
        "WHERE ingested_at >= {since:DateTime64(3, 'UTC')} "
        "AND raw_id IN (SELECT raw_id FROM raw.archive) "
        "AND ingest_run_id IN (SELECT run_id FROM meta.ingestion_runs FINAL)",
        parameters={"since": since},
    ).result_rows[0][0])


def fresh_lineage(client, since: datetime) -> dict[str, int]:
    """Count new market and political rows with real raw and run references."""
    return {table: _count(client, table, since) for table in (
        "market.bars", "market.futures_contract_bars", "alt.political_trades"
    )}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=datetime.fromisoformat, required=True)
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.since.tzinfo is None:
        parser.error("--since must include a timezone")
    storage = ClickHouseStorage.from_environment()
    try:
        deadline = time.monotonic() + args.wait_seconds
        while True:
            counts = fresh_lineage(storage.client, args.since)
            if any(counts.values()):
                print(f"Fresh v2 raw-to-curated write verified: {counts}")
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"No fresh v2 raw-to-curated write since {args.since.isoformat()}")
            time.sleep(min(15, max(1, deadline - time.monotonic())))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
