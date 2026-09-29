"""Fail startup unless the separately migrated ClickHouse v2 schema is ready."""

from __future__ import annotations

import logging

from factorlab.clickhouse import ClickHouse
from factorlab.core.logging import configure_logging

REQUIRED_TABLES = {
    "raw.archive",
    "ref.entities",
    "ref.securities",
    "ref.listings",
    "ref.contracts",
    "ref.identifier_aliases",
    "market.bars",
    "market.futures_contract_bars",
    "alt.political_trades",
    "alt.political_filings",
    "alt.political_committees",
    "alt.political_committee_memberships",
    "meta.ingestion_runs",
    "meta.expected_series",
    "meta.session_coverage",
    "meta.recovery_state",
    "meta.source_status",
    "meta.hub_schema_layouts",
}

log = logging.getLogger(__name__)


def check_v2_readiness(client: object) -> None:
    result = client.query(
        "SELECT concat(database, '.', name) FROM system.tables "
        "WHERE database IN ('raw', 'ref', 'market', 'alt', 'meta')"
    )
    present = {row[0] for row in result.result_rows}
    missing = REQUIRED_TABLES - present
    if missing:
        raise RuntimeError(f"ClickHouse v2 schema is incomplete: {', '.join(sorted(missing))}")

    result = client.query(
        "SELECT migration_id, status FROM meta.schema_migrations FINAL "
        "WHERE migration_id = 'wave_09_schema_application_cutover'"
    )
    if not result.result_rows or result.result_rows[0][1] != "succeeded":
        raise RuntimeError("ClickHouse v2 Wave 9 schema has not completed successfully")

    result = client.query(
        "SELECT concat(database, '.', name), sorting_key FROM system.tables "
        "WHERE database = 'meta' AND name IN "
        "('expected_series', 'session_coverage', 'recovery_state')"
    )
    keys = dict(result.result_rows)
    for table in ("meta.expected_series", "meta.session_coverage", "meta.recovery_state"):
        key = keys.get(table, "")
        if "listing_id" not in key or "legacy_instrument_id" in key:
            raise RuntimeError(f"ClickHouse v2 canonical table exchange is incomplete: {table}")


def main() -> None:
    configure_logging(component="schema-migrator", service="bootstrap")
    storage = ClickHouse.from_environment()
    check_v2_readiness(storage.client)
    log.info("ClickHouse v2 schema is ready.")


if __name__ == "__main__":
    main()
