-- Shared canonical layout for the Data Hub schema scratchboard.

CREATE TABLE IF NOT EXISTS hub_schema_layouts (
    layout_id LowCardinality(String),
    revision UInt64,
    schema_fingerprint FixedString(64),
    layout_json String,
    updated_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(revision)
ORDER BY layout_id;
