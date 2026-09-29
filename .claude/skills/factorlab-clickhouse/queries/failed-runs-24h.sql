-- Failed and partial runs in the last 24 hours, newest first.
SELECT run_id, pipeline, source, status, started_at, failed_series,
       substring(ifNull(error, ''), 1, 200) AS error
FROM meta.ingestion_runs FINAL
WHERE started_at >= now() - INTERVAL 1 DAY AND status IN ('failed', 'partial')
ORDER BY started_at DESC
LIMIT 30
