"""Refresh Upstox reference data into ClickHouse before the NSE session."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.countries.in_.equities.upstox.instruments import refresh_all  # noqa: E402
from factorlab.countries.in_.equities.upstox.universes import build_universes  # noqa: E402
from factorlab.storage.clickhouse import ClickHouseStorage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Upstox reference data into ClickHouse")
    parser.add_argument("--exchange", action="append", default=["NSE"])
    args = parser.parse_args()

    storage = ClickHouseStorage.from_environment()
    storage.seed_india_reference_data()
    run = storage.start_ingestion_run(
        pipeline="india_reference_premarket",
        source="upstox",
        requested_series=len(args.exchange),
        metadata={"exchanges": args.exchange},
    )
    raw_ids = {}

    def archive_response(url, body, status, headers, exchange):
        raw_ids[exchange] = storage.archive_http_response(
            source="upstox_instruments",
            source_url=url,
            response_body=body,
            status_code=status,
            response_headers=headers,
            fetch_key=exchange,
            content_type=headers.get("Content-Type", "application/gzip"),
            metadata={"exchange": exchange},
        )

    cache_dir = PROJECT_ROOT / "data" / "upstox" / "instruments"
    try:
        result = refresh_all(cache_dir, tuple(args.exchange), response_observer=archive_response)
        rows_written = 0
        for exchange, instruments in result.items():
            instrument_lookup = storage.sync_instruments(instruments, raw_id=raw_ids.get(exchange))
            contract_lookup = storage.sync_contracts(
                instruments,
                instrument_lookup,
                raw_id=raw_ids.get(exchange),
            )
            rows_written += len(instrument_lookup) + len(contract_lookup)
            print(
                f"{exchange}: synced {len(instrument_lookup)} equities and "
                f"{len(contract_lookup)} futures"
            )

            if exchange == "NSE":
                counts = build_universes(
                    instruments,
                    PROJECT_ROOT / "data" / "in" / "universes",
                )
                print(f"NSE universes: {counts}")
        successful = len(result)
        storage.finish_ingestion_run(
            run,
            status="success" if successful == len(args.exchange) else "partial",
            successful_series=successful,
            failed_series=max(len(args.exchange) - successful, 0),
            rows_written=rows_written,
        )
    except Exception as exc:
        storage.finish_ingestion_run(run, status="failed", error=str(exc)[:2000])
        raise


if __name__ == "__main__":
    main()
