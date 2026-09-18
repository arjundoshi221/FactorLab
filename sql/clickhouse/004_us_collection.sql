CREATE TABLE IF NOT EXISTS us_expected_series (
    instrument_id UUID, symbol String, provider_symbol String, source LowCardinality(String),
    resolution LowCardinality(String), universe String, active Bool,
    version UInt64, ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (source, instrument_id, resolution);

CREATE TABLE IF NOT EXISTS us_recovery_state (
    instrument_id UUID, symbol String, source LowCardinality(String), resolution LowCardinality(String),
    history_complete Bool, available_from Nullable(DateTime64(3, 'UTC')),
    last_bar Nullable(DateTime64(3, 'UTC')), checked_through Nullable(DateTime64(3, 'UTC')),
    full_refreshed_at Nullable(DateTime64(3, 'UTC')), error Nullable(String),
    version UInt64, ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (source, instrument_id, resolution);

CREATE TABLE IF NOT EXISTS us_session_coverage (
    instrument_id UUID, symbol String, source LowCardinality(String), resolution LowCardinality(String),
    trade_date Date, expected UInt16, actual UInt16, missing UInt16,
    version UInt64, ingested_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (source, instrument_id, resolution, trade_date);

CREATE TABLE IF NOT EXISTS us_source_status (
    source String, status String, detail String, checked_at DateTime64(3, 'UTC'), version UInt64
) ENGINE = ReplacingMergeTree(version) ORDER BY source;
