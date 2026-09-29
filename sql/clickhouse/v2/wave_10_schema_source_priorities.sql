-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 10 schema.

CREATE TABLE IF NOT EXISTS ref.source_priorities (
    dataset        LowCardinality(String),        -- 'market.bars', 'market.futures_contract_bars'
    country_code   FixedString(2),
    resolution     LowCardinality(String),        -- '' for datasets without one
    source         LowCardinality(String),        -- provider as written to `source`
    priority       UInt16,                        -- lower wins
    role           LowCardinality(String),        -- 'primary','secondary','shadow','disabled'
    active         Bool,
    synced_at      DateTime64(3, 'UTC'),
    version        UInt64,
    ingested_at    DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (dataset, country_code, resolution, source);
