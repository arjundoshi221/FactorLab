-- Wave 0: namespaces, migration control-plane tables, and resolver contracts.

CREATE DATABASE IF NOT EXISTS ref;
CREATE DATABASE IF NOT EXISTS market;
CREATE DATABASE IF NOT EXISTS fundamentals;
CREATE DATABASE IF NOT EXISTS alt;
CREATE DATABASE IF NOT EXISTS book;
CREATE DATABASE IF NOT EXISTS risk;
CREATE DATABASE IF NOT EXISTS derived;
CREATE DATABASE IF NOT EXISTS broker;
CREATE DATABASE IF NOT EXISTS meta;
CREATE DATABASE IF NOT EXISTS raw;
CREATE DATABASE IF NOT EXISTS research;

CREATE TABLE IF NOT EXISTS meta.schema_migrations (
    migration_id String,
    phase LowCardinality(String),
    wave UInt8,
    checksum FixedString(64),
    status LowCardinality(String),
    run_id UUID,
    started_at DateTime64(3, 'UTC'),
    finished_at Nullable(DateTime64(3, 'UTC')),
    error String,
    version UInt64
) ENGINE = ReplacingMergeTree(version)
ORDER BY migration_id;

CREATE TABLE IF NOT EXISTS meta.migration_runs (
    run_id UUID,
    command String,
    phase LowCardinality(String),
    through_wave UInt8,
    source_database String,
    status LowCardinality(String),
    started_at DateTime64(3, 'UTC'),
    finished_at Nullable(DateTime64(3, 'UTC')),
    error String,
    host String,
    version UInt64
) ENGINE = ReplacingMergeTree(version)
ORDER BY run_id;

CREATE TABLE IF NOT EXISTS meta.migration_id_crosswalk (
    legacy_database String,
    legacy_table String,
    legacy_key String,
    source_hash FixedString(64),
    target_kind LowCardinality(String),
    target_id UUID,
    approved_by String,
    approved_at Nullable(DateTime64(3, 'UTC')),
    evidence String,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (legacy_database, legacy_table, legacy_key, source_hash, target_kind);

CREATE TABLE IF NOT EXISTS meta.migration_reference_enrichment (
    legacy_database String,
    legacy_table String,
    legacy_key String,
    source_hash FixedString(64),
    exchange_code LowCardinality(String),
    mic FixedString(4),
    session_timezone String,
    regular_open String,
    regular_close String,
    currency_code FixedString(3),
    approved_by String,
    approved_at Nullable(DateTime64(3, 'UTC')),
    evidence String,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (legacy_database, legacy_table, legacy_key, source_hash);

CREATE TABLE IF NOT EXISTS meta.migration_political_trade_enrichment (
    legacy_database String,
    legacy_table String,
    legacy_key String,
    source_hash FixedString(64),
    amount_bucket_id LowCardinality(String),
    amount_min Nullable(UInt64),
    amount_max Nullable(UInt64),
    amount_currency FixedString(3) DEFAULT 'USD',
    security_type LowCardinality(String),
    normalized_legislator_name String,
    bioguide_id Nullable(String),
    legislator_entity_id Nullable(UUID),
    listing_id Nullable(UUID),
    security_id Nullable(UUID),
    entity_id Nullable(UUID),
    contract_id Nullable(UUID),
    bioguide_confidence Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    asset_resolution_confidence Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    approved_by String,
    approved_at Nullable(DateTime64(3, 'UTC')),
    evidence String,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (legacy_database, legacy_table, legacy_key, source_hash);
