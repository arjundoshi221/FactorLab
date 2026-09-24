"""Wait for a fresh raw-to-curated v2 write after release activation."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.storage.clickhouse import ClickHouseStorage  # noqa: E402


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


def universe_ready(client, since: datetime) -> bool:
    status = client.query(
        "SELECT count() FROM meta.source_status FINAL "
        "WHERE country_code = 'US' AND source = 'universe' "
        "AND status = 'ready' AND checked_at >= {since:DateTime64(3, 'UTC')}",
        parameters={"since": since},
    ).result_rows[0][0]
    active = client.query(
        "SELECT count() FROM meta.expected_series FINAL "
        "WHERE country_code = 'US' AND source = 'schwab' "
        "AND resolution = 'daily' AND active"
    ).result_rows[0][0]
    return bool(status and active >= 450)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=datetime.fromisoformat, required=True)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--require-universe-ready", action="store_true")
    args = parser.parse_args()
    if args.since.tzinfo is None:
        parser.error("--since must include a timezone")
    storage = ClickHouseStorage.from_environment()
    try:
        deadline = time.monotonic() + args.wait_seconds
        while True:
            counts = fresh_lineage(storage.client, args.since)
            ready = not args.require_universe_ready or universe_ready(storage.client, args.since)
            if any(counts.values()) and ready:
                print(f"Fresh v2 raw-to-curated write verified: {counts}; universe_ready={ready}")
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"V2 write or US universe readiness missing since {args.since.isoformat()}: "
                    f"lineage={counts}, universe_ready={ready}"
                )
            time.sleep(min(15, max(1, deadline - time.monotonic())))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
