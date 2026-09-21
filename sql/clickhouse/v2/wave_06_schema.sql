-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 6 schema.

CREATE TABLE IF NOT EXISTS derived.forward_test_attribution (
    trade_date              Date,
    strategy_id             UUID,                    -- FK book.strategies
    broker_code             LowCardinality(String),
    account_id              String,                  -- paper account, FK ref.broker_accounts
    base_currency           FixedString(3),          -- denormalized from ref.broker_accounts
    -- returns
    paper_nav_return        Nullable(Float64),       -- from broker.nav_daily day-over-day
    backtest_return         Nullable(Float64),       -- from derived.backtest_returns
    divergence              Nullable(Float64),       -- paper - backtest
    divergence_pct          Nullable(Float64),       -- divergence / |backtest|
    -- context
    paper_nav               Nullable(Decimal(20,6)),
    backtest_notional       Nullable(Decimal(20,6)),
    n_executions_today      UInt32,
    -- lineage
    computation_id          UUID,                    -- FK derived.computations
    computed_at             DateTime64(3, 'UTC'),
    as_of_time              DateTime64(3, 'UTC'),
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (strategy_id, trade_date, account_id);

CREATE TABLE IF NOT EXISTS derived.computations (
    computation_id       UUID,
    dataset              LowCardinality(String),      -- 'factor_momentum_12_1','signal_value_composite'
    code_version         LowCardinality(String),      -- git sha
    input_query          String,                       -- SQL of what it read
    input_hash           FixedString(64),
    input_row_count      UInt64,
    parameters           String,                       -- JSON
    row_count            UInt64,
    started_at           DateTime64(3, 'UTC'),
    completed_at         Nullable(DateTime64(3, 'UTC')),
    status               LowCardinality(String),
    error                Nullable(String),
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(started_at)
ORDER BY (dataset, code_version, started_at, computation_id);

CREATE TABLE IF NOT EXISTS derived.factors (
    country_code         FixedString(2),
    listing_id           UUID,
    security_id          UUID,
    entity_id            UUID,
    sector_id            LowCardinality(String),      -- denormalized
    factor_id            LowCardinality(String),      -- 'momentum_12_1','value_ep','quality_roic'
    factor_definition_hash FixedString(64),          -- guards against formula drift
    event_date           Date,
    value                Nullable(Float64),
    rank_pct             Nullable(Float32),            -- cross-sectional percentile in universe
    universe_id          LowCardinality(String),      -- context of rank
    -- lineage --
    computation_id       UUID,                         -- FK derived.computations
    code_version         LowCardinality(String),
    input_hash           FixedString(64),
    computed_at          DateTime64(3, 'UTC'),
    as_of_time           DateTime64(3, 'UTC'),
    version              UInt64,
    INDEX idx_listing listing_id TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (factor_id, toYYYYMM(event_date))
ORDER BY (country_code, factor_id, universe_id, event_date, listing_id);
