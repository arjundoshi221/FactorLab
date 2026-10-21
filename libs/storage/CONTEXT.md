# factorlab-storage

Political `recent_filings` accepts an optional filing year, filtered before the limit;
the political bootstrap uses it to keep historical-year PTR requests in scope.

> The DB service: ClickHouse v2 writers used by the production daemons, and the provider-blind dataset sinks and read ports the ingestion engine writes through.

## Purpose

Everything that writes FactorLab data into ClickHouse v2: raw archive, ingestion
runs, identity resolution, canonical UUID minting, provenance and versioning. Production collectors use canonical dataset sinks. The `v2_*.py` classes retain
raw archiving, run lifecycle, inventory, recovery/coverage state and broker writes. Layer:
`LIBRARY_LAYERS["storage"] = {"core", "calendars", "clickhouse", "ingest"}`
([test_boundaries.py](../../tests/architecture/test_boundaries.py)).

## Owns and does not own

- Owns: `ClickHouseStorage` run lifecycle ([clickhouse.py](src/factorlab/storage/clickhouse.py));
  `V2IndiaStorage` ([v2_india.py](src/factorlab/storage/v2_india.py)), `V2USStorage`
  ([v2_us.py](src/factorlab/storage/v2_us.py)), `V2BrokerStorage`
  ([v2_broker.py](src/factorlab/storage/v2_broker.py)),
  `V2ReferenceWriter` ([v2_reference.py](src/factorlab/storage/v2_reference.py));
  UUID namespaces ([canonical_ids.py](src/factorlab/storage/canonical_ids.py));
  duplicate-listing retirement ([identity_repair.py](src/factorlab/storage/identity_repair.py),
  recorded in `ref.listing_migrations` as `duplicate_identity`; resolvers skip retired IDs);
  `ClickHouseSinks` ([sinks/clickhouse.py](src/factorlab/storage/sinks/clickhouse.py))
  with identity, calendar, political and fundamentals helpers in [sinks/](src/factorlab/storage/sinks/__init__.py).
- Does not own: dataset/record/sink contracts (`factorlab-ingest`), connection and
  settings (`factorlab-clickhouse`), DDL (`factorlab-schema`), vendor parsing
  (providers), run scheduling (components, `factorlab-orchestration`).

## Entry points

None (library). Main types: `ClickHouseSinks.from_environment(market)` (`IND` ->
`V2IndiaStorage`, `USA` -> `V2BrokerStorage`, which extends `V2USStorage`, which
extends `V2IndiaStorage`); each `V2*Storage.from_environment()`; sink methods
`upsert_instruments`, `upsert_contracts`, `write_constituents`, `write_bars`,
`write_contract_bars`, `write_snapshot`, `write_legislators`,
`write_political_filings`, `write_political_trades`, `write_fundamentals`,
`sync_source_priorities`; ports `aliases_for`, `natural_refs`, `active_listings`,
`universe_members`, `watermarks`, `load_raw`.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| ClickHouse connection | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USERNAME`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_SSH_*` | see `factorlab-clickhouse` | Through `ClickHouseSettings` |
| Password | secret `CLICKHOUSE_PASSWORD` | `factorlab_dev` (dev) | Secret volume in production |

No direct environment reads in this member.
The retired version-one US master count/publication methods have been removed;
configured US resolution uses SchwabListings and canonical reference sinks.

## Data

**Shared profiles:** `ProfileStorage.sync` retains omitted records and existing strategy/closed-date fields. `V2BrokerStorage` records complete snapshots, including empty ones. `PortfolioBookStorage` validates active profile/catalog ownership and durable orders before writing catalog replacements or append-only assignment events; fills remain unchanged. [Feature](../../docs/features/F-036-portfolio-tagging-pnl.md).

- Writes: `raw.archive` (gzip body, `response_sha256`, `content_encoding = 'gzip'`),
  `meta.ingestion_runs` (`source_channel` = the run's `source`), `meta.unresolved_entities`
  (parked rows), `meta.expected_series`, `meta.recovery_state`, `meta.session_coverage`,
  `meta.source_status`, `ref.entities`, `ref.securities`, `ref.listings`,
  `ref.contracts`, `ref.identifier_aliases`, `ref.universes`,
  `ref.universe_membership`, `ref.legislator_terms`, `ref.source_priorities`,
  `market.bars`, `market.futures_contract_bars`, `market.futures_quotes`, `alt.political_filings`,
  `alt.political_trades`, `alt.political_committees`,
  `alt.political_committee_memberships`, `fundamentals.filings`,
  `fundamentals.line_items`, `broker.positions_snapshot`,
  `broker.account_state_snapshot`, `broker.executions`, `broker.open_orders_snapshot`.
- Reads for enrichment: `ref.exchanges`, `ref.currencies`, `ref.broker_metrics_map`,
  `ref.execution_methods`.
- `country_code` is `IN` for `V2IndiaStorage` and `US` for `V2USStorage` and subclasses.
- Legacy defaults that are frozen strings: `V2USStorage` methods default
  `source="schwab"` or `source="eodhd"`; broker alias kind `ibkr_conid`. Pipeline
  names (`india_intraday_1min`, `us_live`, ...) are passed in by components.
- Schema: [06-schema-rehau.md](../../docs/architecture/06-schema-rehau.md).

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-clickhouse`, `factorlab-ingest`,
  `factorlab-calendars`. Third party: `pandas>=2.1`, `tzdata`.
- Lineage: sinks refuse writes without an active run, or when
  `provenance.ingest_run_id` differs from it (`_check_lineage`).
- Identity (sinks): alias -> ISIN -> exchange + symbol; only `authoritative` mode
  mints; misses are parked in `meta.unresolved_entities`, never dropped.
- Every row gets `version = factorlab.clickhouse.version(now)`.
- Sinks never branch on `provenance.source`; `ClickHouseSinks` and `InMemorySink`
  must behave identically (sink conformance suite).

## Observability

`start_ingestion_run` writes a `running` row and binds `run_id`, `pipeline`, `source`
into log context; `finish_ingestion_run` writes the terminal status (`success`,
`partial`, `failed`, `cancelled`) and unbinds. Query runs, parked entities and source
status with the [factorlab-clickhouse](../../.claude/skills/factorlab-clickhouse/SKILL.md)
skill; follow a run's log lines with [factorlab-logs](../../.claude/skills/factorlab-logs/SKILL.md).

## Tests

`uv run pytest libs/storage` (sink conformance against `InMemorySink` and
`ClickHouseSinks` over `factorlab.testkit.fake_clickhouse.FakeClickHouse`, source
priorities, v2 India/US/reference writers, raw archive). No test needs a server.

## Release and rollback

Never released alone. Components whose closure includes it
([affected.py](../../tools/affected.py)): `ingest-broker`, `ingest-india`,
`ingest-political`, `ingest-us`, all `rollback: writer` with `data_contract: 1`.
A change that alters written rows or keys is a data-contract change; image rollback
does not undo rows already written.

## Pitfalls

- Rule R3: no provider names as string literals in `factorlab/storage/`. The existing
  ones in `v2_*.py` are ratcheted in
  [boundary_allowlist.txt](../../tests/architecture/boundary_allowlist.txt) and may
  only shrink; `storage/sinks/` must stay clean.
- `FakeClickHouse` answers only the queries the sinks issue; a new query must be
  taught to the fake in `factorlab-testkit` or conformance fails loudly.
- Canonical UUID namespaces in `canonical_ids.py` are frozen; changing one re-keys
  every identity.
- Bar session and `trade_date` tagging lives in `sinks/calendar.py` (period bars keyed
  at exchange-local midnight, as production writes); keep it in step with the hours in
  `factorlab-calendars` / orchestration.

Replay requests binary archive bodies before gzip decoding. Listing resolution
accepts a MIC and preserves the existing exchange code and UUID. Market inserts
group by country, resolution and five-year window (at most 60 monthly partitions).

**Indexes (F-042):**

- `ClassificationSinkMixin` ([sinks/classification.py](src/factorlab/storage/sinks/classification.py))
  writes `ref.security_sector_history`, seeds missing `ref.sectors` names, and writes
  `fundamentals.shares_outstanding`. Unresolved sector tickers are parked; they do not
  block the snapshot.
- `IndexStorage` ([indexes.py](src/factorlab/storage/indexes.py)):
  - reads universe and sector spans, shares observations, and daily closes from
    `market.bars` for a caller-given source, one calendar year per query;
  - writes `derived.index_levels`, `derived.index_weights` (100k-row batches) and
    `derived.computations`.
- `seed_us_reference_data` also adds the pseudo venue `US Index` (MIC `INDX`) for index
  values.

**US futures (F-043):** `ClickHouseSinks.write_contract_quotes` resolves contracts by alias and writes `market.futures_quotes` with `trade_date = globex.session_date(observed_at)`, parking unresolved ones. `V2USStorage.seed_us_reference_data` also seeds `US_FUTURES_EXCHANGES` (`XCME`, `XCBT`, `XNYM`, `XCEC`, `XCBF`, `IFUS`; sessions 18:00-17:00) with `exchange_code` equal to the MIC, because `upsert_future` requires a contract to share its root listing's exchange code. `futures_session_closes(day, source=)` returns each contract's last snapshot in a session; `futures_settlements(day, after=, source=)` returns the first settlement reported in a later session at or after `after`.
