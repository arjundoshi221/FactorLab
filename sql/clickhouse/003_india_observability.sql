-- Operational state used by the India collection dashboard.

CREATE TABLE IF NOT EXISTS india_expected_series (
    instrument_id UUID,
    contract_id UUID,
    symbol LowCardinality(String),
    source LowCardinality(String),
    universe LowCardinality(String),
    resolution LowCardinality(String),
    active Bool,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (source, instrument_id, contract_id);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id UUID,
    market_code LowCardinality(String),
    pipeline LowCardinality(String),
    source LowCardinality(String),
    universe LowCardinality(String),
    status LowCardinality(String),
    started_at DateTime64(3, 'UTC'),
    completed_at Nullable(DateTime64(3, 'UTC')),
    requested_series UInt32,
    successful_series UInt32,
    failed_series UInt32,
    rows_written UInt64,
    error Nullable(String),
    metadata_json String,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(started_at)
ORDER BY (market_code, pipeline, started_at, run_id);
