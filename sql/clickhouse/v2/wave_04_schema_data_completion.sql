-- Additive destinations for legacy rows that cannot fit market.bars or the
-- original v2 operational-state tables. No continuous futures roll is inferred.

CREATE TABLE IF NOT EXISTS market.futures_contract_bars (
    country_code FixedString(2),
    underlying_listing_id UUID,
    contract_id UUID,
    legacy_instrument_id UUID,
    legacy_contract_id UUID,
    source_symbol LowCardinality(String),
    resolution LowCardinality(String),
    session LowCardinality(String),
    bar_time DateTime64(3, 'UTC'),
    trade_date Date,
    open Nullable(Decimal(18, 6)),
    high Nullable(Decimal(18, 6)),
    low Nullable(Decimal(18, 6)),
    close Nullable(Decimal(18, 6)),
    volume Nullable(UInt64),
    oi Nullable(UInt64),
    source LowCardinality(String),
    raw_id Nullable(UUID),
    ingest_run_id UUID,
    as_of_time DateTime64(3, 'UTC'),
    ingested_at DateTime64(3, 'UTC'),
    source_hash FixedString(64),
    migrated_at DateTime64(3, 'UTC'),
    version UInt64
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(bar_time)
ORDER BY (country_code, contract_id, resolution, bar_time, source);

CREATE TABLE IF NOT EXISTS meta.expected_series (
    country_code FixedString(2),
    listing_id UUID,
    contract_id Nullable(UUID),
    legacy_instrument_id UUID,
    legacy_contract_id UUID,
    source_table LowCardinality(String),
    symbol String,
    provider_symbol Nullable(String),
    source LowCardinality(String),
    universe LowCardinality(String),
    resolution LowCardinality(String),
    active Bool,
    source_hash FixedString(64),
    version UInt64,
    ingested_at DateTime64(3, 'UTC'),
    migrated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (source_table, source, legacy_instrument_id, legacy_contract_id, resolution);

CREATE TABLE IF NOT EXISTS meta.session_coverage (
    country_code FixedString(2),
    listing_id UUID,
    legacy_instrument_id UUID,
    symbol String,
    source LowCardinality(String),
    resolution LowCardinality(String),
    trade_date Date,
    expected UInt16,
    actual UInt16,
    missing UInt16,
    source_hash FixedString(64),
    version UInt64,
    ingested_at DateTime64(3, 'UTC'),
    migrated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (source, legacy_instrument_id, resolution, trade_date);

CREATE TABLE IF NOT EXISTS meta.recovery_state (
    country_code FixedString(2),
    listing_id UUID,
    legacy_instrument_id UUID,
    symbol String,
    source LowCardinality(String),
    resolution LowCardinality(String),
    history_complete Bool,
    available_from Nullable(DateTime64(3, 'UTC')),
    last_bar Nullable(DateTime64(3, 'UTC')),
    checked_through Nullable(DateTime64(3, 'UTC')),
    full_refreshed_at Nullable(DateTime64(3, 'UTC')),
    error Nullable(String),
    source_hash FixedString(64),
    version UInt64,
    ingested_at DateTime64(3, 'UTC'),
    migrated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (source, legacy_instrument_id, resolution);

CREATE TABLE IF NOT EXISTS meta.source_status (
    country_code FixedString(2),
    source String,
    status String,
    detail String,
    checked_at DateTime64(3, 'UTC'),
    source_hash FixedString(64),
    version UInt64,
    migrated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (country_code, source);
