# ClickHouse v2 remaining legacy data

## Application cutover preparation (2026-09-24)

Wave 9 is preparatory and is **not an application cutover**. Its schema file
creates `meta.expected_series_canonical`, `meta.session_coverage_canonical`,
`meta.recovery_state_canonical`, and `meta.hub_schema_layouts`; it also adds
defaults so live inserts can omit migration provenance on
`market.futures_contract_bars` and `meta.source_status`. It does not rename the
operational tables. The Wave 9
backfill copies their current `FINAL` rows and the legacy hub layout. The
ClickHouse migration runner repeats successful backfill files, so applying
`backfill --through-wave 9` also repeats Waves 1–4; do this only while legacy
writers are stopped and before any v2 writer is activated. Reruns can add
physical duplicates, so compare logical rows with `FINAL`.

The replacement tables use canonical listing and contract IDs in their
`ReplacingMergeTree` keys. Before exchanging names, verify source and target
logical counts and compare each canonical identity's latest values, including
active state, dates, coverage counts, and errors. Check for duplicate legacy
IDs mapping to one canonical identity. Any mismatch is a no-go; resume legacy
collection. Keep Wave 8 RBAC pending. The application, API, UI, release order,
and live v2 writes are not yet converted, so do not perform the exchange or
activate a v2 release from this preparatory change.

The Wave 4 data-completion extension moves legacy rows that could not fit the
original v2 tables. It is additive. The running US and India ingest containers
still write to the `factorlab` database; legacy remains authoritative, and no
application cutover or RBAC change is part of this migration.

| Legacy source | V2 destination | Identity and preservation |
| --- | --- | --- |
| Contract-keyed India futures in `market_candles_1min` | `market.futures_contract_bars` | Approved contract and instrument crosswalks resolve canonical IDs. The source contract IDs, symbol, raw ID, timestamps, hash, OHLCV, and OI remain available. No continuous roll is computed. |
| `india_expected_series`, `us_expected_series` | `meta.expected_series` | Both use an approved listing crosswalk; India futures also use an approved contract crosswalk. Country, source table, legacy IDs, and provider symbol where available are retained. |
| `us_session_coverage` | `meta.session_coverage` | Listing ID is resolved; source counts, date, audit timestamp, and legacy instrument ID are retained. |
| `us_recovery_state` | `meta.recovery_state` | Listing ID is resolved; all recovery dates and error text are retained. |
| `us_source_status` | `meta.source_status` | Checked time and status text are retained; migration time is separate. |

The extension uses separate checksum-tracked migration IDs rather than changing
already-applied Wave 4 files. Apply `schema` through Wave 4 first, then run
`validate --through-wave 4`, then apply `backfill` through Wave 4. These
commands must be run with the matching SQL package and the production migration
lock; mutations require `--yes`. No RBAC phase is needed. Compare source and
destination counts with `FINAL`, check source hashes and canonical foreign keys,
then rerun the backfill to verify logical idempotency. Later catch-up runs must
repeat validation because legacy writers remain active.

Production `us_session_coverage` spans January 1985 through September 2026
(501 monthly partitions). The default ClickHouse limit of 100 partitions per
insert block is too low for its single-statement backfill. Set
`CLICKHOUSE_MIGRATION_MAX_PARTITIONS_PER_INSERT_BLOCK=1000` only on the
one-off migration client when applying the backfill. The migration journal can
resume a failed attempt safely; reruns compare source hashes and versions.

The source's uncontracted minute and daily bars remain in `market.bars`.
`market.futures_continuous` is reserved for a later explicit roll computation;
it is not a safe storage target for raw contract bars.

## Production schema status (2026-09-24)

The checksum-tracked schema phase has been applied through Wave 8. Waves 5–8
added 18 concrete tables across `fundamentals`, `derived`, `broker`, `book`, and
`risk`, plus the `research.owned_listings` view. The migration journal reports
all schema files through Wave 8 succeeded; validation through Wave 8 passed.
The Wave 8 RBAC phase remains pending by choice, and no application containers
were changed.

These later-wave tables are empty: the legacy `factorlab` database has no
corresponding fundamentals, derived, broker, book, or risk source tables.
Creating these schemas does not implement the spec's new provider ingesters,
derived computations, broker reconciliation, attribution, risk tooling, or
deferred materialized views. Legacy ingestion remains authoritative; use a
separate reviewed plan before wiring writers or readers to the new namespaces.
