# ClickHouse v2 data completion and application cutover

## September 25, 2026 cutover (Asia/Kolkata)

The release is gated on complete Waves 1–4 history and the final Wave 9
operational-table copy. Verify the September 24 production schema report below
again against production. Waves 5–8 have no matching legacy history. Keep the
current application ClickHouse credential for this release; the Wave 8 RBAC
file lacks required `ref`, `raw`, and `meta` ingest grants.

1. Confirm a clean, synchronized `main`, checksum journal, approved current-hash
   crosswalks, source and `FINAL` destination counts, canonical references,
   free disk space, and representative API results. Run migration `status` and
   `validate --through-wave 9`. Confirm the application credential can access
   all required v2 tables.
2. Stop `ingest-india`, `ingest-us`, and `universe-us` and remove the political
   cron. Record the cutover watermark and confirm no new legacy inserts. Run
   deterministic candidate staging and approval for any newly changed source
   hashes; inspect the pending target IDs and political correction differences.
   Reparse political PDFs with `stage_clickhouse_v2_political.py`; stage SEC
   listing evidence where needed. `approve_clickhouse_v2_political.py` refuses
   changes to previously approved semantic corrections. Run final validation
   and `apply backfill --through-wave 9 --yes` with
   `CLICKHOUSE_MIGRATION_MAX_PARTITIONS_PER_INSERT_BLOCK=1000` on that one-off
   process. Successful backfill files repeat; do not run them after v2 writes.
3. Run `python scripts/cutover_clickhouse_v2.py check`. It compares `FINAL`
   counts and latest-row values in both directions for the three operational
   tables. Any difference is a no-go. Then run
   `python scripts/cutover_clickhouse_v2.py exchange --yes` and rerun `check`.
   It refuses mixed or already exchanged layouts, so a rerun cannot swap names
   back by accident.
4. Start the new API and check v2 overview, India, US, political, and schema
   map reads. Then activate universe, India, and US writers and political cron
   through the release helper. Confirm fresh `raw.archive`, `ref.*`,
   `market.bars`, `market.futures_contract_bars`, `meta.*`, and `alt.*` rows,
   matching raw and run IDs, coverage and recovery continuity, and no new
   legacy inserts. The release helper waits for a fresh raw-to-curated v2 row
   with valid raw and run references. Check every active destination over the
   next collection cycles.

Before the first v2 writer activation, a failed gate means resume legacy
collection and postpone release. If names were exchanged, reverse the three
exchanges before another Wave 9 backfill. After v2 writer activation, stop the
affected writer and release a v2 fix. The release helper requires paused
legacy writers and cron for first activation and does not run migrations.

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
collection. Keep Wave 8 RBAC pending. Exchange names only under the cutover
gates above.

The Wave 4 data-completion extension moves legacy rows that could not fit the
original v2 tables. It is additive. Until the gated cutover, running US and
India ingest containers write to `factorlab` and legacy remains authoritative.

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
