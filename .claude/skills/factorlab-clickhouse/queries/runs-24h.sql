-- Runs in the last 24 hours, by pipeline, source and status.
SELECT pipeline, source, status, count() AS runs, max(started_at) AS last_started,
       sum(rows_written) AS rows_written, sum(failed_series) AS failed_series
FROM meta.ingestion_runs FINAL
WHERE started_at >= now() - INTERVAL 1 DAY
GROUP BY pipeline, source, status
ORDER BY pipeline, source, status
