-- Prepare canonical-keyed operational tables without changing applied waves.
-- The replacements are populated and exchanged only after the cutover watermark.

ALTER TABLE market.futures_contract_bars
    MODIFY COLUMN legacy_instrument_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000');
ALTER TABLE market.futures_contract_bars
    MODIFY COLUMN legacy_contract_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000');
ALTER TABLE market.futures_contract_bars
    MODIFY COLUMN source_hash FixedString(64) DEFAULT '';
ALTER TABLE market.futures_contract_bars
    MODIFY COLUMN migrated_at DateTime64(3, 'UTC') DEFAULT toDateTime64(0, 3, 'UTC');
ALTER TABLE meta.source_status
    MODIFY COLUMN source_hash FixedString(64) DEFAULT '';
ALTER TABLE meta.source_status
    MODIFY COLUMN migrated_at DateTime64(3, 'UTC') DEFAULT toDateTime64(0, 3, 'UTC');

CREATE TABLE IF NOT EXISTS meta.expected_series_canonical (
    country_code FixedString(2),
    listing_id UUID,
    contract_id Nullable(UUID),
    legacy_instrument_id Nullable(UUID),
    legacy_contract_id Nullable(UUID),
    source_table Nullable(String),
    symbol String,
    provider_symbol Nullable(String),
    source LowCardinality(String),
    universe LowCardinality(String),
    resolution LowCardinality(String),
    active Bool,
    source_hash Nullable(FixedString(64)),
    version UInt64,
    ingested_at DateTime64(3, 'UTC'),
    migrated_at Nullable(DateTime64(3, 'UTC'))
) ENGINE = ReplacingMergeTree(version)
ORDER BY (country_code, listing_id, contract_id, source, resolution)
SETTINGS allow_nullable_key = 1;

CREATE TABLE IF NOT EXISTS meta.session_coverage_canonical (
    country_code FixedString(2),
    listing_id UUID,
    legacy_instrument_id Nullable(UUID),
    symbol String,
    source LowCardinality(String),
    resolution LowCardinality(String),
    trade_date Date,
    expected UInt16,
    actual UInt16,
    missing UInt16,
    source_hash Nullable(FixedString(64)),
    version UInt64,
    ingested_at DateTime64(3, 'UTC'),
    migrated_at Nullable(DateTime64(3, 'UTC'))
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (country_code, listing_id, source, resolution, trade_date);

CREATE TABLE IF NOT EXISTS meta.recovery_state_canonical (
    country_code FixedString(2),
    listing_id UUID,
    legacy_instrument_id Nullable(UUID),
    symbol String,
    source LowCardinality(String),
    resolution LowCardinality(String),
    history_complete Bool,
    available_from Nullable(DateTime64(3, 'UTC')),
    last_bar Nullable(DateTime64(3, 'UTC')),
    checked_through Nullable(DateTime64(3, 'UTC')),
    full_refreshed_at Nullable(DateTime64(3, 'UTC')),
    error Nullable(String),
    source_hash Nullable(FixedString(64)),
    version UInt64,
    ingested_at DateTime64(3, 'UTC'),
    migrated_at Nullable(DateTime64(3, 'UTC'))
) ENGINE = ReplacingMergeTree(version)
ORDER BY (country_code, listing_id, source, resolution);

CREATE TABLE IF NOT EXISTS meta.hub_schema_layouts (
    layout_id LowCardinality(String),
    revision UInt64,
    schema_fingerprint FixedString(64),
    layout_json String,
    updated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(revision)
ORDER BY layout_id;
