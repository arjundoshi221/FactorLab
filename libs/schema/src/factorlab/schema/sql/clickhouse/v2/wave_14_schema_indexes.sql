-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 14 schema.

CREATE TABLE IF NOT EXISTS ref.security_sector_history (
    listing_id      UUID,
    classification  LowCardinality(String),   -- 'gics'
    sector_id       LowCardinality(String),   -- FK ref.sectors (GICS level 2: '10'..'60')
    effective_from  Date,                     -- inclusive
    effective_to    Nullable(Date),           -- inclusive; NULL = current
    source          LowCardinality(String),
    raw_id          Nullable(UUID),
    version         UInt64,
    ingested_at     DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (classification, listing_id, effective_from);

CREATE TABLE IF NOT EXISTS fundamentals.shares_outstanding (
    listing_id         UUID,
    as_of_date         Date,
    shares_outstanding UInt64,
    market_cap         Nullable(Float64),      -- vendor-reported, vendor units
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
) ENGINE = ReplacingMergeTree(version)
ORDER BY (listing_id, as_of_date, source);

CREATE TABLE IF NOT EXISTS derived.index_levels (
    index_code           LowCardinality(String),
    trade_date           Date,
    level                Float64,
    ret_1d               Nullable(Float64),
    n_constituents       UInt32,
    coverage             Float32,               -- priced share of start-of-day weight
    complete             Bool,
    methodology_version  LowCardinality(String),
    computation_id       UUID,
    computed_at          DateTime64(3, 'UTC'),
    version              UInt64
) ENGINE = ReplacingMergeTree(version)
ORDER BY (index_code, trade_date);

CREATE TABLE IF NOT EXISTS derived.index_weights (
    index_code      LowCardinality(String),
    trade_date      Date,
    listing_id      UUID,
    weight          Float64,                    -- start-of-day weight
    ret_1d          Nullable(Float64),
    contribution    Float64,
    computation_id  UUID,
    version         UInt64
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYear(trade_date)
ORDER BY (index_code, trade_date, listing_id);
