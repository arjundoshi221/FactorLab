-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 3 schema.

CREATE TABLE IF NOT EXISTS meta.ingestion_runs (
    run_id               UUID,
    country_code         LowCardinality(String),      -- '*' for global
    pipeline             LowCardinality(String),
    source               LowCardinality(String),
    source_channel       LowCardinality(String),
    universe_id          LowCardinality(String),
    status               LowCardinality(String),
    started_at           DateTime64(3, 'UTC'),
    completed_at         Nullable(DateTime64(3, 'UTC')),
    requested_series     UInt32,
    successful_series    UInt32,
    failed_series        UInt32,
    rows_written         UInt64,
    listing_ids_touched  Array(UUID),                  -- which listings this batch wrote to
    error                Nullable(String),
    metadata_json        String,
    parent_run_id        Nullable(UUID),               -- for cascading pipelines
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(started_at)
ORDER BY (country_code, pipeline, source, started_at, run_id);

CREATE TABLE IF NOT EXISTS meta.lineage (
    dataset              LowCardinality(String),      -- 'market.bars','fundamentals.line_items','derived.factors'
    row_business_key     String,                       -- hash of (listing_id, resolution, bar_time, source) etc.
    source_raw_ids       Array(UUID),
    computation_id       Nullable(UUID),
    produced_at          DateTime64(3, 'UTC'),
    produced_by          LowCardinality(String),      -- 'ingest','backfill','recovery','recompute'
)
ENGINE = MergeTree
PARTITION BY (dataset, toYYYYMM(produced_at))
ORDER BY (dataset, row_business_key, produced_at)
TTL toDate(produced_at) + INTERVAL 90  DAY  DELETE WHERE startsWith(dataset, 'market.'),
    toDate(produced_at) + INTERVAL 2   YEAR DELETE WHERE startsWith(dataset, 'alt.');

CREATE TABLE IF NOT EXISTS meta.reconciliation_drift (
    detected_at         DateTime64(3, 'UTC'),
    broker_code         LowCardinality(String),
    account_id          String,
    account_mode        LowCardinality(String),
    drift_type          LowCardinality(String),   -- 'position_qty','position_missing','position_extra',
                                                   --   'nav','cash','open_order'
    security_key        Nullable(String),          -- vendor_id or listing_id::text for position drifts
    prev_snapshot_time  DateTime64(3, 'UTC'),      -- snapshot we compared against
    curr_snapshot_time  DateTime64(3, 'UTC'),      -- snapshot that revealed the drift
    expected_value      Nullable(String),          -- string-typed to accept any drift shape
    observed_value      Nullable(String),
    delta               Nullable(String),          -- pre-computed human-readable diff
    severity            LowCardinality(String),    -- 'info','warn','critical'
    -- resolution audit
    resolved_at         Nullable(DateTime64(3, 'UTC')),
    resolved_by         LowCardinality(Nullable(String)),  -- 'auto_next_snapshot','manual','ignore'
    resolution_note     Nullable(String),
    -- lineage
    ingest_run_id       UUID,
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, toYYYYMM(detected_at))
ORDER BY (broker_code, account_id, detected_at);

CREATE TABLE IF NOT EXISTS meta.unresolved_entities (
    first_seen          DateTime64(3, 'UTC'),
    last_seen           DateTime64(3, 'UTC'),
    source              LowCardinality(String),   -- FK ref.sources (which ingester surfaced it)
    alias_kind          LowCardinality(String),   -- matches ref.identifier_aliases.alias_kind
    alias_value         String,                    -- the raw vendor id
    scope_country       Nullable(FixedString(2)),
    scope_exchange      LowCardinality(Nullable(String)),
    context_json        String,                    -- caller-provided context (row hash, security_type guess, etc.)
    occurrence_count    UInt32,                    -- how many facts referenced this unresolved id
    -- resolution attempts
    retry_count         UInt32,
    last_retry_at       Nullable(DateTime64(3, 'UTC')),
    resolved_at         Nullable(DateTime64(3, 'UTC')),
    resolved_target_kind LowCardinality(Nullable(String)),  -- 'entity','security','listing','contract'
    resolved_target_id   Nullable(UUID),
    resolved_by          LowCardinality(Nullable(String)),  -- 'auto_resolver','manual_override'
    resolution_note      Nullable(String),
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (source, alias_kind, alias_value);
