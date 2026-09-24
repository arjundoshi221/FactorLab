INSERT INTO meta.ingestion_runs
SELECT
    r.run_id,
    transform(r.market_code, ['USA', 'IND', 'ALT_POLITICAL'], ['US', 'IN', 'US'], r.market_code),
    r.pipeline, r.source, r.source, r.universe, r.status, r.started_at, r.completed_at,
    r.requested_series, r.successful_series, r.failed_series, r.rows_written,
    CAST([], 'Array(UUID)'), r.error, r.metadata_json, NULL, r.version, r.ingested_at
FROM {{source_database}}.ingestion_runs AS r FINAL
LEFT JOIN meta.ingestion_runs AS target FINAL ON target.run_id = r.run_id
WHERE target.run_id = toUUID('00000000-0000-0000-0000-000000000000')
   OR r.version > target.version;

