-- Latest status per country and source.
SELECT country_code, source, status, substring(detail, 1, 160) AS detail, checked_at
FROM meta.source_status FINAL
ORDER BY country_code, source
LIMIT 30
