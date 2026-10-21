-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 15 schema.

CREATE TABLE IF NOT EXISTS market.futures_quotes (
    country_code          FixedString(2),
    underlying_listing_id UUID,
    contract_id           UUID,
    source_symbol         LowCardinality(String),
    observed_at           DateTime64(6, 'UTC'),      -- vendor quote time
    trade_date            Date,                      -- Globex session date
    last_price            Nullable(Decimal(18,6)),
    last_size             Nullable(UInt32),
    last_trade_at         Nullable(DateTime64(6, 'UTC')),
    bid                      Nullable(Decimal(18,6)),
    ask                      Nullable(Decimal(18,6)),
    bid_size                 Nullable(UInt32),
    ask_size                 Nullable(UInt32),
    day_open                 Nullable(Decimal(18,6)),
    day_high                 Nullable(Decimal(18,6)),
    day_low                  Nullable(Decimal(18,6)),
    day_volume            Nullable(UInt64),
    oi                    Nullable(UInt64),
    settlement_price      Nullable(Decimal(18,6)),
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (country_code, contract_id, source, observed_at);
