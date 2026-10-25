# ClickHouse memory and part count

The VPS has 7.6 GiB of RAM and no swap. ClickHouse shares it with every other service,
so it must fail a query before the kernel OOM killer stops the whole server.

## What happened (October 2026)

The kernel OOM-killed ClickHouse about 274 times in two days. Queries and merges were
small. The memory went on bookkeeping for data parts: about 55 KB of server memory per
part, outdated parts included.

- The weekly US daily refresh re-read every listing's history from 1970 and rewrote
  all of it, unchanged bars included, with a coverage row for every trading day.
- `market.bars` and `meta.session_coverage` partitioned daily history by month, about
  680 partitions each. ClickHouse never merges across partitions, so one listing's
  rewrite created about 1,360 tiny parts.
- Merged-away parts waited the default 480 s before deletion. 50,000 outdated parts
  held about 3 GiB.
- The server's default limit (0.9 of the 6 GiB container, about 5.4 GiB) sat above what
  the host could give, so the kernel killed it first.

## What prevents it now

| Layer | Change |
|---|---|
| Server | [memory.xml](../../deploy/clickhouse/config.d/memory.xml): `max_server_memory_usage` 4.5 GiB, `old_parts_lifetime` 60 s |
| Writers | ingest-us writes only changed bars and coverage days, plus the last 7 days ([collector](../../components/ingest-us/CONTEXT.md)) |
| Raw archive | an identical response to the same request within 30 days reuses its `raw_id` |
| Layout | Wave 16 partitions daily history by year (about 57 partitions instead of 682) |
| Alert | the monitor warns `clickhouse: memory pressure` at 15,000 parts or 85% of the limit |

## Diagnose

Run these read-only queries with `docker exec -i factorlab-clickhouse clickhouse-client -n`
(add `SET readonly = 2, max_memory_usage = 300000000;` first):

```sql
-- Memory and parts right now
SELECT uptime(),
       formatReadableSize((SELECT value FROM system.metrics WHERE metric = 'MemoryTracking')),
       (SELECT value FROM system.metrics WHERE metric = 'PartsActive'),
       (SELECT value FROM system.metrics WHERE metric = 'PartsOutdated');

-- Which tables hold the parts
SELECT database || '.' || table, uniqExact(partition) AS partitions,
       countIf(active) AS active_parts, count() AS all_parts
FROM system.parts GROUP BY database, table ORDER BY all_parts DESC LIMIT 10;

-- Who is creating parts (last 10 minutes)
SELECT database || '.' || table, count() FROM system.part_log
WHERE event_type = 'NewPart' AND event_time > now() - INTERVAL 10 MINUTE
GROUP BY database, table ORDER BY count() DESC LIMIT 10;
```

On the host, `sudo journalctl -k --since -3h | grep 'Killed process.*clickhouse'` counts
kernel OOM kills, and `docker inspect -f '{{.RestartCount}}' factorlab-clickhouse` shows
restarts.

## Respond

1. If outdated parts dominate, wait: they are freed after `old_parts_lifetime`.
2. If one writer creates most new parts, stop that service
   (`sudo factorlab-compose stop <service>`) and find what it rewrites.
3. Do not raise the memory limit above 4.5 GiB without freeing host memory first.

## Repartition (Wave 16)

Run during a weekend, when no market is open:

1. `factorlab-db migrate status`, then `migrate apply --phase schema --through-wave 16 --yes`.
2. Stop `ingest-us`, `indexes-us` and `ingest-india`, the writers to `market.bars` and coverage.
3. `factorlab-db repartition plan`, then `repartition apply --yes`. It refuses while
   either table had writes in the last 120 s and swaps nothing unless the `FINAL` row
   count and key hash match.
4. Start the writers again. The `*_v16` tables hold the pre-swap data as the rollback.
   Swapping back is `EXCHANGE TABLES market.bars AND market.bars_v16` (same for
   coverage), with writers stopped. Drop the `*_v16` tables after a week of normal running.
