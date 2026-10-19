-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 12 schema.

CREATE TABLE IF NOT EXISTS ref.app_users (
    user_id UUID,                   -- Stable application profile ID, not a broker login
    display_name String,            -- Human-readable tester name; renaming preserves user_id
    active Bool,                    -- Administrative lifecycle flag; not an access permission
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY user_id;

CREATE TABLE IF NOT EXISTS ref.broker_account_owners (
    broker_code LowCardinality(String), -- Broker namespace, combined with account_id
    account_id String,                 -- Native broker account ID; SIM_ reserved for simulation
    user_id UUID,                      -- Logical FK ref.app_users
    simulated Bool,                    -- Synthetic responses, not actual Gateway data
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (broker_code, account_id);
