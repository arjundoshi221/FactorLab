# ClickHouse v2 data completion and application cutover

## Production cutover procedure (completed for September 25, 2026 collection)

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
original v2 tables. It is additive. Before the gated cutover, US and India
ingest containers wrote to `factorlab`; v2 is now authoritative.

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
then rerun the backfill to verify logical idempotency. Preactivation catch-up
runs repeated validation while legacy writers remained active. Do not rerun a
backfill after the first v2 live write.

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

The checksum-tracked schema phase has been applied through Wave 9. Waves 5–8
added 18 concrete tables across `fundamentals`, `derived`, `broker`, `book`, and
`risk`, plus the `research.owned_listings` view. The migration journal reports
all schema files through Wave 9 succeeded; validation through Wave 9 passed
after reviewing and approving current-hash candidates. A Wave 1–4 catch-up
was run while legacy writers remained active. At that audit watermark, legacy
India minute rows equaled v2 equity plus contract-futures rows (5,900,572),
US minute and daily rows matched (49,920 and 21,968), and political trades
matched (496). Legacy political membership snapshots (124,600) are compressed
into 3,925 v2 SCD periods by the Wave 4 backfill, so raw row counts differ by
design. The legacy India and US writers and political cron were stopped at
`2026-09-24T16:08:42Z`. The paused-writer final backfill completed, including
the raw archives and ingestion runs that arrived after the earlier catch-up.
The three Wave 9 replacement tables matched their source tables exactly under
`FINAL` and were exchanged; public `meta.expected_series`,
`meta.session_coverage`, and `meta.recovery_state` now have canonical keys.
Migration validation through Wave 9 passed again after the backfill. No v2
application writer had been activated at this checkpoint. Free ClickHouse disk
was about 74 GB at preflight.

The Wave 8 RBAC phase remains pending by choice. Application activation and
post-cutover checks are recorded below.

These later-wave tables are empty: the legacy `factorlab` database has no
corresponding fundamentals, derived, broker, book, or risk source tables.
Creating these schemas does not implement the spec's new provider ingesters,
derived computations, broker reconciliation, attribution, risk tooling, or
deferred materialized views. The v2 application write path is authoritative;
use a separate reviewed plan before wiring new workloads to those namespaces.

## Production application activation (2026-09-24)

Release `20260924T170537Z-f5061f3eaea8` completed at
`2026-09-24T17:11:12Z` with image digest
`sha256:c439223edd910c399ce5b24b1afc4b18b29d683ab865a8f2be49da7793ffe605`.
The API, secret agent, universe, India, and US containers were running on that
digest, and political cron was installed for 02:15 UTC daily. The v2 activation
marker is set; never run the legacy backfill or restore legacy writers now.

Post-activation checks found 503 active US daily series, 198,996 fresh v2
market bars with valid raw and run references at the audit time, and new US
coverage and recovery rows. A manual run of the scheduled political job wrote
113 trade rows with raw and run lineage; `alt.political_trades FINAL` increased
from 496 to 498 logical trades. The overview, schema map, India, US, and
political hub routes responded; US instrument and candle pagination returned
canonical `listing_id` values. Legacy India minute, US minute, US daily, and
political trade counts remained at their cutover watermark values. The Cboe
BZX exchange and CBOE listing resolved to MIC `BATS` using the
[Cboe `Z=BZX` feed code](https://www.cboe.com/document/tech-spec/content/technical-specifications/cboe-titanium-cboe-one-equities-feed-specification/cboe-one-update-messages-udp--tcp/openingclosing-price-message-fields)
and the [published BZX MIC](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX%3A02016R1646-20221017).
The release workflow and its backend and frontend jobs passed.

The India market had closed before activation. During the September 25 India
session, confirm fresh equity and contract-futures bars with raw and run IDs,
coverage and recovery continuity, API and UI results, and unchanged legacy
counts. Continue checking the US and political cycles on v2; fix any failure
forward without replaying migration backfills.

The first production `full_nse_eq` release activated 2,670 cash equity series
but no futures. The subsequent collection fix adds the nearest contract for
each resolved single-stock future (210 in the September 24 Upstox master).
Check that these contract series are active before the market opens, then
verify their first live writes during the session.

## September 25 live-session audit

At 09:59 UTC, `market.bars FINAL` held 436,573 India equity bars for the
session across 2,660 listings, and `market.futures_contract_bars FINAL` held
76,259 bars across all 210 active contracts. Every audited bar had a matching
`raw.archive` row and `meta.ingestion_runs FINAL` row. The latest India runs
were successful. The legacy India minute, US minute, US daily, and political
trade counts remained at their cutover watermarks. The API overview, India,
US, and political dashboards returned HTTP 200. All application containers
were running the same pinned v2 image without restarts.

The September 25 political cron attempt failed because the deployed script
lost its executable mode. A locked manual catch-up completed at 10:02 UTC and
archived the latest legislative and filing responses. The cron now invokes
the script with `/bin/sh`, and releases reinstall the cron entry. Recheck the
next scheduled execution.

India intraday bars are live, but no India rows exist in `meta.session_coverage`
or `meta.recovery_state`. Their current keys also lack `contract_id`, so they
cannot represent separate futures contract coverage or recovery state. This
remains an open operational-schema and writer task; do not describe India
coverage or recovery continuity as verified. US daily recovery also had two
symbols blocked by pre-1970 Schwab bars falling outside the configured
exchange calendar. A fix to skip those out-of-range bars is being released;
verify the US source status becomes `ready` afterward.
