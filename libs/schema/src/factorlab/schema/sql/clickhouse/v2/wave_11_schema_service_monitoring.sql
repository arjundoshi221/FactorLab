-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 11 schema.

CREATE TABLE IF NOT EXISTS meta.service_events (
    received_at DateTime64(3, 'UTC'),
    sent_at DateTime64(3, 'UTC'),
    service_id LowCardinality(String),
    instance_id String,
    seq UInt64,
    type LowCardinality(String),
    origin LowCardinality(String),
    state LowCardinality(String),
    severity LowCardinality(String),
    error_type LowCardinality(String),
    message String,
    context_json String,
    host String,
    skew_ms Int64
) ENGINE = MergeTree
PARTITION BY toYYYYMM(received_at)
ORDER BY (service_id, received_at)
TTL toDateTime(received_at) + INTERVAL 14 DAY DELETE WHERE type = 'hb',
    toDateTime(received_at) + INTERVAL 180 DAY DELETE WHERE type != 'hb';

CREATE TABLE IF NOT EXISTS meta.service_status (
    service_id LowCardinality(String),
    status LowCardinality(String),
    last_seen_at Nullable(DateTime64(3, 'UTC')),
    instance_id String,
    instance_started_at Nullable(DateTime64(3, 'UTC')),
    restarts_24h UInt32,
    last_error_at Nullable(DateTime64(3, 'UTC')),
    last_error_type LowCardinality(String),
    last_error_message String,
    warn_count_1h UInt32,
    err_count_1h UInt32,
    updated_at DateTime64(3, 'UTC'),
    version UInt64,
    packet_loss UInt64,
    registered Bool
) ENGINE = ReplacingMergeTree(version)
ORDER BY service_id;
