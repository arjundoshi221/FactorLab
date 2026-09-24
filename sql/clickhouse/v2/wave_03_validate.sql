-- check: unsupported legacy ingestion-run market codes
SELECT count() FROM {{source_database}}.ingestion_runs FINAL
WHERE market_code NOT IN ('USA', 'IND', 'ALT_POLITICAL');

-- check: duplicate legacy ingestion-run IDs after source deduplication
SELECT count() FROM (
    SELECT run_id FROM {{source_database}}.ingestion_runs FINAL
    GROUP BY run_id HAVING count() > 1
);
