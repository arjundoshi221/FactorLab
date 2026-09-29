-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 2 schema.

CREATE TABLE IF NOT EXISTS market.bars (
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
PARTITION BY (product_type, resolution, toYYYYMM(bar_time))
ORDER BY (country_code, product_type, listing_id, resolution, source, bar_time);

CREATE TABLE IF NOT EXISTS market.options_bars (
    -- identity --
    country_code           FixedString(2),
    contract_id            UUID,                     -- FK ref.contracts
    underlying_listing_id  UUID,
    underlying_security_id UUID,
    -- option key (denormalized) --
    expiry                 Date,
    strike                 Decimal(18,6),
    right                  LowCardinality(String),   -- 'C' / 'P'
    exercise_style         LowCardinality(String),
    multiplier             UInt32,
    -- time & shape --
    resolution               LowCardinality(String),
    session                  LowCardinality(String),
    bar_time                 DateTime64(3, 'UTC'),
    trade_date               Date,       -- same conventions as bars
    -- OHLCV --
    open                     Nullable(Decimal(18,6)),
    high                     Nullable(Decimal(18,6)),
    low                      Nullable(Decimal(18,6)),
    close                    Nullable(Decimal(18,6)),
    volume                   Nullable(UInt64),
    oi                       Nullable(UInt64),
    turnover                 Nullable(Decimal(24,4)),
    -- greeks + IV --
    iv                       Nullable(Float64),
    delta                    Nullable(Float64),
    gamma                    Nullable(Float64),
    vega                     Nullable(Float64),
    theta                    Nullable(Float64),
    rho                      Nullable(Float64),
    underlying_price         Nullable(Decimal(18,6)),  -- for reproducibility
    -- provenance + audit (same as bars) --
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    latency_ms               Nullable(Int32),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (toYYYYMM(expiry), toYYYYMM(bar_time))  -- TODO measure at Wave 5+ (F15 rev 11):
                                                     --   5y × 12 active expiries × 12 bar months ≈ 720
                                                     --   partitions plus weeklies; on the edge. If part
                                                     --   count grows too much, drop to
                                                     --   (toYear(expiry), toYYYYMM(bar_time)).
ORDER BY (country_code, underlying_listing_id, expiry, right, strike, resolution, bar_time);

CREATE TABLE IF NOT EXISTS market.futures_continuous (
    country_code       FixedString(2),
    underlying_listing_id UUID,                       -- 'the front NIFTY future' concept
    roll_method        LowCardinality(String),        -- 'volume','open_interest','first_notice','custom_5d_before_expiry'
    roll_position      LowCardinality(String),        -- 'c1','c2','c3','...'  (front, back, ...)
    resolution         LowCardinality(String),
    session            LowCardinality(String),
    bar_time           DateTime64(3, 'UTC'),
    trade_date         Date,
    active_contract_id UUID,                          -- which contract this row came from
    open                     Nullable(Decimal(18,6)),
    high                     Nullable(Decimal(18,6)),
    low                      Nullable(Decimal(18,6)),
    close                    Nullable(Decimal(18,6)),
    volume                   Nullable(UInt64),
    oi                       Nullable(UInt64),
    roll_adjustment    Decimal(18,6),                 -- cumulative price adjustment applied
    computation_id     UUID,
    computed_at        DateTime64(3, 'UTC'),
    as_of_time         DateTime64(3, 'UTC'),
    version            UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (roll_method, toYYYYMM(bar_time))
ORDER BY (country_code, underlying_listing_id, roll_method, roll_position, bar_time);

CREATE TABLE IF NOT EXISTS market.quotes (
    country_code       FixedString(2),
    listing_id         UUID,
    security_id        UUID,
    entity_id          UUID,
    observed_at        DateTime64(6, 'UTC'),
    -- last trade at observation time --
    last_price         Nullable(Decimal(18,6)),
    last_size          Nullable(UInt32),
    last_trade_at      Nullable(DateTime64(6, 'UTC')),
    -- book state --
    bid                      Nullable(Decimal(18,6)),
    ask                      Nullable(Decimal(18,6)),
    bid_size                 Nullable(UInt32),
    ask_size                 Nullable(UInt32),
    -- day accumulators (from vendor) --
    day_open                 Nullable(Decimal(18,6)),
    day_high                 Nullable(Decimal(18,6)),
    day_low                  Nullable(Decimal(18,6)),
    day_vwap                 Nullable(Decimal(18,6)),
    day_volume         Nullable(UInt64),
    day_turnover       Nullable(Decimal(24,4)),
    -- context --
    session            LowCardinality(String),
    -- futures snapshots --
    oi                 Nullable(UInt64),
    settlement_price   Nullable(Decimal(18,6)),
    -- provenance --
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    ingested_at              DateTime64(3, 'UTC'),
    latency_ms               Nullable(Int32),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (country_code, listing_id, source, observed_at)
TTL toDate(observed_at) + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS market.fx_rates (
    base_currency      FixedString(3),
    quote_currency     FixedString(3),
    resolution         LowCardinality(String),        -- '1min','1h','daily'
    fix_convention     LowCardinality(String),        -- 'spot','wm_reuters_4pm_london','ecb','close'
    bar_time           DateTime64(3, 'UTC'),
    trade_date         Date,
    open                     Nullable(Decimal(18,10)),
    high                     Nullable(Decimal(18,10)),
    low                      Nullable(Decimal(18,10)),
    close                    Nullable(Decimal(18,10)),
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(bar_time)
ORDER BY (base_currency, quote_currency, resolution, fix_convention, bar_time);
