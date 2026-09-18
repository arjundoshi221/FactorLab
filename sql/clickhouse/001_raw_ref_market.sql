-- FactorLab ClickHouse foundation: raw archive, reference data, and market data.
-- Apply with: python scripts/bootstrap_clickhouse.py

CREATE TABLE IF NOT EXISTS raw_http_archive (
    raw_id UUID,
    source LowCardinality(String),
    source_url String,
    fetch_key String,
    status_code UInt16,
    response_headers String,
    response_body String CODEC(ZSTD(6)),
    content_type LowCardinality(String),
    content_encoding LowCardinality(String),
    response_sha256 FixedString(64),
    fetched_at DateTime64(3, 'UTC'),
    as_of_time DateTime64(3, 'UTC'),
    metadata_json String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(fetched_at)
ORDER BY (source, fetched_at, raw_id);

CREATE TABLE IF NOT EXISTS ref_countries (
    country_code FixedString(2),
    name String,
    region LowCardinality(String),
    timezone String,
    source LowCardinality(String),
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY country_code;

CREATE TABLE IF NOT EXISTS ref_exchanges (
    exchange_code LowCardinality(String),
    name String,
    country_code FixedString(2),
    market_code LowCardinality(String),
    currency_code FixedString(3),
    timezone String,
    source LowCardinality(String),
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY exchange_code;

CREATE TABLE IF NOT EXISTS ref_instruments (
    instrument_id UUID,
    instrument_key String,
    trading_symbol String,
    name String,
    isin Nullable(String),
    exchange_code LowCardinality(String),
    segment LowCardinality(String),
    instrument_type LowCardinality(String),
    asset_class LowCardinality(String),
    country_code FixedString(2),
    market_code LowCardinality(String),
    currency_code FixedString(3),
    lot_size UInt32,
    tick_size Nullable(Decimal(18, 6)),
    freeze_quantity Nullable(Decimal(18, 3)),
    exchange_token Nullable(String),
    status LowCardinality(String),
    first_seen Date,
    last_seen Date,
    source LowCardinality(String),
    raw_id Nullable(UUID),
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY instrument_key;

CREATE TABLE IF NOT EXISTS ref_contracts (
    contract_id UUID,
    contract_key String,
    instrument_id UUID,
    trading_symbol String,
    contract_type LowCardinality(String),
    segment LowCardinality(String),
    expiry Nullable(Date),
    strike_price Nullable(Decimal(18, 2)),
    lot_size UInt32,
    tick_size Nullable(Decimal(18, 6)),
    weekly Bool,
    status LowCardinality(String),
    first_seen Date,
    last_seen Date,
    source LowCardinality(String),
    raw_id Nullable(UUID),
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY contract_key;

CREATE TABLE IF NOT EXISTS market_candles_1min (
    instrument_id UUID,
    contract_id UUID,
    symbol LowCardinality(String),
    market_code LowCardinality(String),
    bar_time DateTime64(3, 'UTC'),
    open Nullable(Decimal(18, 6)),
    high Nullable(Decimal(18, 6)),
    low Nullable(Decimal(18, 6)),
    close Nullable(Decimal(18, 6)),
    volume Nullable(UInt64),
    oi Nullable(UInt64),
    source LowCardinality(String),
    raw_id Nullable(UUID),
    as_of_time DateTime64(3, 'UTC'),
    ingested_at DateTime64(3, 'UTC'),
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(bar_time)
ORDER BY (instrument_id, bar_time, source, contract_id);

CREATE TABLE IF NOT EXISTS market_candles_daily (
    instrument_id UUID,
    contract_id UUID,
    symbol LowCardinality(String),
    market_code LowCardinality(String),
    trade_date Date,
    open Nullable(Decimal(18, 6)),
    high Nullable(Decimal(18, 6)),
    low Nullable(Decimal(18, 6)),
    close Nullable(Decimal(18, 6)),
    adj_close Nullable(Decimal(18, 6)),
    volume Nullable(UInt64),
    source LowCardinality(String),
    raw_id Nullable(UUID),
    as_of_time DateTime64(3, 'UTC'),
    ingested_at DateTime64(3, 'UTC'),
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (instrument_id, trade_date, source, contract_id);
