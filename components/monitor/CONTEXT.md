# monitor

> UDP service liveness and error monitoring.

## Purpose

Track service liveness using receive time and persist history independently of notification delivery.

## Owns and does not own

Owns protocol receiver, state transitions and batched persistence. Runtime owns senders; API/web own the hub display; schema owns DDL.

## Entry points

`factorlab-monitor [--registry PATH] [--log-dir PATH] [--port 9125]`. UDP on backend only; local HTTP `/healthz` on 9126.

## Configuration and secrets

CLICKHOUSE_* settings and CLICKHOUSE_PASSWORD from monitor_runtime. Registry defaults to /app/monitor-services.yaml. Warning alert threshold: 10 in 5 minutes. Silence deadlines come from the registry. Every 5 minutes a short-lived connection reads ClickHouse `PartsActive`, `PartsOutdated`, `MemoryTracking` and `max_server_memory_usage`, and alerts `clickhouse: memory pressure` once per episode at 15,000 parts or 85% of the limit ([runbook](../../docs/operations/clickhouse-memory.md)).

## Data

INSERT/SELECT meta.service_events and meta.service_status; SELECT system.metrics and system.server_settings for the ClickHouse probe. Failed inserts spill pending-*.jsonl under /var/log/factorlab/monitor and replay on recovery. Delivery is at least once after ambiguous database failures.

## Dependencies and contracts

core, runtime, clickhouse, PyYAML. Protocol v1 golden fixtures shared with runtime. Monitor instance status updates every flush (5 seconds or 500 events).

## Observability

JSON logs component/service monitor. Invalid packets counted and sampled by size; contents never logged. Healthcheck probes the event loop. API flags a stale watcher after 15 seconds.

## Tests

`uv run pytest components/monitor libs/runtime` covers packet fixtures, liveness, restarts, errors and storage outage replay.

## Release and rollback

Per-component monitor/vX.Y.Z; writer with data_contract 1. Install platform registry and pending wave 11 before enabling senders. No migrations run at startup.

## Pitfalls

UDP has no delivery guarantee. The private backend network is the trust boundary. Alerting continues during ClickHouse failures. Stopped services stay stopped until a new packet arrives. Active windows gate deadline evaluation, using local timezone and ISO weekdays.
