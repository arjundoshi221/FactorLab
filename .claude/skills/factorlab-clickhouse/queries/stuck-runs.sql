-- Runs still marked running two hours after they started (likely died mid-run).
SELECT run_id, pipeline, source, started_at, dateDiff('minute', started_at, now()) AS minutes
FROM meta.ingestion_runs FINAL
WHERE status = 'running' AND started_at < now() - INTERVAL 2 HOUR
  AND started_at >= now() - INTERVAL 7 DAY
ORDER BY started_at DESC
LIMIT 30
