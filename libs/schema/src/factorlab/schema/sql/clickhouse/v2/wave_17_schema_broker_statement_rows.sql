-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 17 schema.

CREATE TABLE IF NOT EXISTS broker.statement_rows (
    generated_at       DateTime64(3, 'UTC'),        -- when the broker generated the statement
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    statement_from     Date,
    statement_to       Date,
    section            LowCardinality(String),      -- 'OpenPositions', 'MTDYTDPerformanceSummary', ...
    element            LowCardinality(String),      -- 'OpenPosition', ...
    row_key            String,
    level_of_detail    LowCardinality(String),      -- 'SUMMARY', 'EXECUTION', ... or ''
    asset_category     LowCardinality(String),      -- 'STK', 'OPT', ... or ''
    vendor_id          String,                      -- IBKR conid, '' when none
    trading_symbol     String,
    currency           LowCardinality(String),
    report_date        Nullable(Date),
    attributes         Map(String, String),         -- every attribute as reported
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, toYYYYMM(statement_to))
ORDER BY (broker_code, account_id, statement_to, section, row_key);
