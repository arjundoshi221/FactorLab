-- Wave 16: yearly partitions for period-resolution history (hand-written forward wave).
-- Monthly partitions put ~680 partitions under one listing's daily history since 1970,
-- so each history write created ~680 parts per table; at ~55 KB of server memory per
-- part the weekly US refresh OOM-killed ClickHouse (2026-10). These tables copy the live
-- market.bars (wave 2) and meta.session_coverage (the wave 9 canonical layout) with only
-- the partition key changed. `factorlab-db repartition apply` copies the live rows in and
-- swaps the names with EXCHANGE TABLES; the *_v16 names then hold the pre-swap data as
-- the rollback until an operator drops them.

CREATE TABLE IF NOT EXISTS market.bars_v16 (
    -- identity --
    country_code       FixedString(2),
    listing_id         UUID,
    security_id        UUID,                          -- denormalized
    entity_id          UUID,                          -- denormalized (may be NULL for indices)
    product_type       LowCardinality(String),        -- 'common','preferred','adr','etf','etn','reit',
                                                       --   'warrant','index','index_future',
                                                       --   'single_stock_future','vol_future','vol_etp'
    -- time & shape --
    resolution         LowCardinality(String),        -- '5s','1min','5min','15min','1h','daily','weekly','monthly'
    session            LowCardinality(String),        -- 'regular','pre','post','open_auction','close_auction'
    bar_time           DateTime64(3, 'UTC'),          -- bar OPEN time (convention)
    trade_date         Date,                          -- session-local date, denormalized for pruning
    -- OHLC + volume --
    open                     Nullable(Decimal(18,6)),
    high                     Nullable(Decimal(18,6)),
    low                      Nullable(Decimal(18,6)),
    close                    Nullable(Decimal(18,6)),
    volume             Nullable(UInt64),               -- shares (spot) OR contracts (derivative)
    turnover           Nullable(Decimal(24,4)),        -- listing-currency notional
    trades_count       Nullable(UInt32),
    -- derivatives-only --
    oi                 Nullable(UInt64),               -- open interest, contracts
    settlement_price   Nullable(Decimal(18,6)),        -- exchange settlement
    -- cross-product-safe --
    -- notional_usd is NOT stored here — a raw notional_usd would be frozen at
    --   ingest-time FX. It is computed at read time by the market.bars_notional_usd MV
    --   (see §13.8). Removed from this DDL in rev 10 to resolve a contradiction
    --   with the prose below. (Fix F16 — trivial, rev 10.)
    -- provenance --
    source             LowCardinality(String),
    source_channel     LowCardinality(String),
    raw_id             Nullable(UUID),
    ingest_run_id      UUID,
    -- audit --
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    latency_ms         Nullable(Int32),
    version            UInt64,
    -- skip indices: session and source_channel are commonly filtered but low-cardinality,
    -- so they earn a skip index instead of sort-key positions.
    INDEX idx_session         session         TYPE set(8)               GRANULARITY 4,
    INDEX idx_source_channel  source_channel  TYPE set(32)              GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (
    product_type,
    resolution,
    if(resolution IN ('daily', 'weekly', 'monthly'), toYear(bar_time), toYYYYMM(bar_time))
)
ORDER BY (country_code, product_type, listing_id, resolution, source, bar_time);

CREATE TABLE IF NOT EXISTS meta.session_coverage_v16 (
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
PARTITION BY toYear(trade_date)
ORDER BY (country_code, listing_id, source, resolution, trade_date);
