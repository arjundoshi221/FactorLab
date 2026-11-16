"""Link every IBKR instrument in stored Flex statements to its FactorLab listing.

A Flex sync links the instruments that today's statement describes. Names that
appear now only in summary sections (closed positions) carry no venue or ISIN, so
they need the identity IBKR reported in earlier statements. This command reads the
newest stored identity per conid from ``broker.statement_rows`` and runs it through
the same ``link_instruments`` sink path, inside its own ``meta.ingestion_runs`` row
(pipeline ``broker.instrument_link:ibkr``). It is idempotent: linked conids are
skipped, so rerunning only picks up what is still missing.

Usage:
    factorlab-ingest-broker link-instruments [--conid 123 ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from factorlab.core.logging import configure_logging
from factorlab.ingest.datasets import InstrumentRecord, WriteResult
from factorlab.ingest.provider import RawCapture, ingestion_run
from factorlab.sources.ibkr.instruments import instrument_record

BROKER_CODE = "ibkr"
PIPELINE = "broker.instrument_link:ibkr"
SOURCE_CHANNEL = "ibkr:instrument_link"
# Only rows that name a venue and currency carry a usable identity (summary rows
# often lack one); a row with an ISIN beats a newer one without.
REQUIRED_ATTRIBUTES = ("conid", "listingExchange", "currency")
PREFERRED_ATTRIBUTE = "isin"

log = logging.getLogger("factorlab.ibkr.link_instruments")


def records_from(identities: Sequence[Mapping[str, str]]) -> list[InstrumentRecord]:
    """One record per conid (stocks and ETFs on mapped venues; the rest is logged)."""
    records = (instrument_record(attributes) for attributes in identities)
    return list({record.ref: record for record in records if record is not None}.values())


def link(
    sink: Any,
    identities: Sequence[Mapping[str, str]],
    *,
    now: datetime | None = None,
) -> tuple[list[InstrumentRecord], WriteResult]:
    """Link the stored identities inside one ingestion run; returns the records and result."""
    now = now or datetime.now(UTC)
    records = records_from(identities)
    body = json.dumps(
        sorted(record.ref.alias_value for record in records), separators=(",", ":")
    ).encode()
    with ingestion_run(
        sink,
        pipeline=PIPELINE,
        source=BROKER_CODE,
        market_code="USA",
        requested_series=len(records),
    ) as ctx:
        raw_id = ctx.archive(
            RawCapture(
                body,
                f"conids:{hashlib.sha256(body).hexdigest()[:16]}",
                "file",
                now,
                content_type="application/json",
            ),
            source_channel=SOURCE_CHANNEL,
        )
        provenance = ctx.provenance(source_channel=SOURCE_CHANNEL, raw_id=raw_id, as_of_time=now)
        result = (
            sink.link_instruments(records, provenance=provenance) if records else WriteResult(0)
        )
        ctx.succeed_unit("link", result.rows_written)
    return records, result


def _describe(record: InstrumentRecord) -> str:
    ref = record.ref
    return (
        f"{ref.alias_value} {ref.trading_symbol} {ref.exchange_code} "
        f"isin={ref.isin or '-'} type={record.product_type}"
    )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="factorlab-ingest-broker link-instruments",
        description="Link IBKR conids from stored Flex statements to FactorLab listings.",
    )
    parser.add_argument("--conid", action="append", default=[], help="only these conids")
    parser.add_argument("--dry-run", action="store_true", help="read and parse; write nothing")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(
        component="ingest-broker", service="ibkr-link-instruments", level=args.log_level
    )

    from factorlab.storage.sinks import ClickHouseSinks
    from factorlab.storage.sinks.identity import IdentityResolver
    from factorlab.storage.v2_broker import V2BrokerStorage

    storage = V2BrokerStorage.from_environment()
    identities = storage.statement_identities(
        BROKER_CODE, required=REQUIRED_ATTRIBUTES, prefer=PREFERRED_ATTRIBUTE, vendor_ids=args.conid
    )
    print(f"stored identities: {len(identities)}")
    if args.dry_run:
        for record in records_from(identities):
            print("would link", _describe(record))
        return 0
    records, result = link(ClickHouseSinks(storage), identities)
    print(
        f"records: {len(records)} written: {result.rows_written} "
        f"unresolved: {result.unresolved} already resolved: {result.resolved}"
    )
    known = IdentityResolver(storage.client).listings(record.ref for record in records)
    missing = [record for record in records if record.ref not in known]
    for record in missing:
        print("still unlinked", _describe(record))
    return 1 if missing else 0
