-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 13 schema.

ALTER TABLE book.themes ADD COLUMN IF NOT EXISTS user_id Nullable(UUID);

ALTER TABLE book.strategies ADD COLUMN IF NOT EXISTS user_id Nullable(UUID);

ALTER TABLE book.trade_names ADD COLUMN IF NOT EXISTS user_id Nullable(UUID);

ALTER TABLE book.trade_names MODIFY COLUMN theme_id Nullable(UUID);

CREATE TABLE IF NOT EXISTS book.order_assignment_events (
    event_id UUID,
    user_id UUID,
    broker_code LowCardinality(String),
    account_id String,
    perm_id Int64,
    theme_id Nullable(UUID),
    strategy_id Nullable(UUID),
    trade_name_id Nullable(UUID),
    changed_at DateTime64(6, 'UTC'),
    CONSTRAINT durable_order CHECK perm_id > 0,
    CONSTRAINT complete_assignment CHECK
        (isNull(theme_id) AND isNull(strategy_id) AND isNull(trade_name_id)) OR
        (isNotNull(theme_id) AND isNotNull(strategy_id) AND isNotNull(trade_name_id))
) ENGINE = MergeTree
PARTITION BY toYYYYMM(changed_at)
ORDER BY (broker_code, account_id, perm_id, changed_at, event_id);
