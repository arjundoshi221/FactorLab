---
name: factorlab-clickhouse
description: Run bounded, read-only ClickHouse queries against FactorLab production (through a temporary SSH tunnel) to check ingestion runs, source status and freshness, and cross-link a run_id with its logs. Use when the user asks what production ingested, whether a pipeline is stale or failing, or what one run wrote; only on the user's request.
allowed-tools: Bash(uv run python scripts/read_clickhouse.py:*), Bash(python scripts/read_clickhouse.py:*)
---

# Reading FactorLab production ClickHouse

`scripts/read_clickhouse.py` opens a temporary SSH tunnel to the VPS loopback and
runs one `SELECT` or `WITH` query. Results are capped at 30 rows and 12 KiB. The
host key must already be trusted, and credentials come from `.env` or the
environment (never print or copy them).

**Only when the user asks for a live read** (AGENTS.md: no live ClickHouse queries
without a user request). Never write, never run DDL, never widen a query to scan raw
tables without a time bound. ClickHouse is never exposed publicly. The tunnel is the
only path, and host-key checking stays on.

## Canned queries (files next to this skill)

```bash
uv run python scripts/read_clickhouse.py --sql-file .claude/skills/factorlab-clickhouse/queries/runs-24h.sql
uv run python scripts/read_clickhouse.py --sql-file .claude/skills/factorlab-clickhouse/queries/failed-runs-24h.sql
uv run python scripts/read_clickhouse.py --sql-file .claude/skills/factorlab-clickhouse/queries/stuck-runs.sql
uv run python scripts/read_clickhouse.py --sql-file .claude/skills/factorlab-clickhouse/queries/source-status.sql
```

One run, by the `run_id` from a log line (`factorlab-logs` skill):

```bash
uv run python scripts/read_clickhouse.py --sql "SELECT run_id, pipeline, source, source_channel, status, started_at, completed_at, requested_series, successful_series, failed_series, rows_written, substring(ifNull(error, ''), 1, 300) AS error FROM meta.ingestion_runs FINAL WHERE run_id = toUUID('<run_id>')"
```

## What the tables mean

- `meta.ingestion_runs` has one row per run (ReplacingMergeTree, so always read it
  with `FINAL`). `status` is one of `running`, `success`, `partial`, `failed` or
  `cancelled`. `pipeline`, `source` and `source_channel` are frozen names
  (`india_intraday_1min`, `us_live`, ...). `run_id` is also bound to every log line
  that the run wrote.
- `meta.source_status` holds the latest status per (`country_code`, `source`), also
  read with `FINAL`.
- A `running` row older than the pipeline's cadence usually means the process died
  mid-run. Check the component's logs around `started_at`.

## Cross-checking with logs

1. Start from a failed or stuck run here and take its `run_id`.
2. Run `uv run python tools/read_logs.py run --run-id <run_id>`. It prints every log
   line of that run, across components.
3. Or go the other way: from a log `run_id`, use the one-run query above to see what
   the run recorded.
