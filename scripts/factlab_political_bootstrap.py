"""Ingest public congressional references and House STOCK Act filings."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.sources.political.house_clerk import (
    fetch_and_parse_ptr,
    fetch_house_filing_index,
)
from factorlab.sources.political.references import fetch_reference_snapshot
from factorlab.storage.political_clickhouse import (
    build_name_resolver,
    resolve_filing_bioguide,
)
from factorlab.storage.v2_political import (
    V2PoliticalClickHouseStorage as PoliticalClickHouseStorage,
)
from factorlab.storage.v2_us import V2USStorage as ClickHouseStorage


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest public congressional references and House PTRs"
    )
    parser.add_argument("--year", type=int, default=datetime.now(UTC).year)
    parser.add_argument(
        "--ptr-limit",
        type=int,
        default=0,
        help="Download and parse at most N newest PTR PDFs; 0 stores the index only",
    )
    parser.add_argument("--congress", type=int, default=119)
    args = parser.parse_args()

    base_storage = ClickHouseStorage.from_environment()
    storage = PoliticalClickHouseStorage(base_storage)
    run = base_storage.start_ingestion_run(
        market_code="ALT_POLITICAL",
        pipeline="political_bootstrap",
        source="political",
        universe="house",
        requested_series=4 + max(args.ptr_limit, 0),
        metadata={"year": args.year, "congress": args.congress, "ptr_limit": args.ptr_limit},
    )
    try:
        successful, rows_written = _run_ingestion(args, base_storage, storage)
        requested = 4 + max(args.ptr_limit, 0)
        failed = max(requested - successful, 0)
        base_storage.finish_ingestion_run(
            run,
            status="success" if failed == 0 else "partial",
            successful_series=successful,
            failed_series=failed,
            rows_written=rows_written,
        )
    except Exception as exc:
        base_storage.finish_ingestion_run(run, status="failed", error=str(exc)[:2000])
        raise


def _run_ingestion(args, base_storage, storage) -> tuple[int, int]:
    """Run political ingestion and return successful work units and written rows."""

    snapshot = fetch_reference_snapshot(base_storage)
    legislator_lookup = storage.sync_legislators(
        snapshot.legislators,
        raw_id=snapshot.raw_ids["legislators"],
    )
    committee_lookup = storage.sync_committees(
        snapshot.committees,
        raw_id=snapshot.raw_ids["committees"],
        congress_number=args.congress,
    )
    membership_count = storage.sync_memberships(
        snapshot.memberships,
        legislator_lookup=legislator_lookup,
        committee_lookup=committee_lookup,
        raw_id=snapshot.raw_ids["memberships"],
        snapshot_date=datetime.now(UTC).date(),
        congress_number=args.congress,
    )
    print(
        f"Synced {len(legislator_lookup)} legislators, "
        f"{len(committee_lookup)} committees/subcommittees, "
        f"and {membership_count} memberships"
    )
    rows_written = len(legislator_lookup) + len(committee_lookup) + membership_count

    filings, filing_raw_id = fetch_house_filing_index(args.year, base_storage)
    name_resolver = build_name_resolver(snapshot.legislators)
    filing_count = storage.write_house_filings(
        filings,
        raw_id=filing_raw_id,
        name_resolver=name_resolver,
    )
    resolved_count = sum(
        resolve_filing_bioguide(filing, name_resolver) is not None
        for filing in filings
    )
    print(
        f"Synced {filing_count} House PTR filing-index rows "
        f"({resolved_count} legislator matches)"
    )
    rows_written += filing_count

    if args.ptr_limit <= 0:
        return 4, rows_written

    trade_count = 0
    selected = sorted(
        filings,
        key=lambda filing: filing["filing_date"],
        reverse=True,
    )[:args.ptr_limit]
    for filing in selected:
        trades, ptr_raw_id = fetch_and_parse_ptr(filing, base_storage)
        trade_count += storage.write_trades(
            trades,
            filing=filing,
            raw_id=ptr_raw_id,
            bioguide_id=resolve_filing_bioguide(filing, name_resolver),
        )
        print(
            f"PTR {filing['filing_id']}: parsed {len(trades)} transactions"
        )
    print(f"Parsed and stored {trade_count} House transactions")
    return 4 + len(selected), rows_written + trade_count


if __name__ == "__main__":
    main()
