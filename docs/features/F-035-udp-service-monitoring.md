---
id: F-035
title: Heartbeat, warning and error monitoring over UDP
status: review
priority: P1
components: [runtime, ingest, schema, platform, api, web]
owner: unassigned
sprint: null
decisions: [ADR-0014, ADR-0015]
links: [F-026, F-003, F-007]
created: 2026-09-30
updated: 2026-09-30
---

# F-035 Heartbeat, warning and error monitoring over UDP

## Problem

When a daemon hangs, dies or restarts, nobody is told.
[heartbeat.py](../../libs/runtime/src/factorlab/runtime/heartbeat.py) only touches a file's
mtime inside its own container. The only thing that reads that file is the per-container
`factorlab-healthcheck` probe, and the "canary" its docstring promises was never built.
Nothing wires `supervised(on_crash=…)` to an alert, and `uptime-kuma` in
`deploy/compose.base.yml` has never been started. So we find errors by reading logs.

Strategies and a trade engine are coming, and their language is not yet chosen. They will
need the same liveness and error signal, so the signal cannot be tied to Python.

## Scope

A separate `monitor` container. Every ingestor, strategy and service sends it UDP
datagrams: periodic **heartbeats**, plus **warnings** and **errors**. The monitor decides
whether each service is up, stores the history in ClickHouse, alerts through the existing
`notify()`, and a hub page shows the result.

```text
ingest-* / universe-us / ibkr-snapshot / api / strategy-* / trade-engine
      │  UDP JSON datagrams → monitor:9125 (factorlab-backend network only)
      ▼
monitor ──► ClickHouse meta.service_events / meta.service_status
   └─────► notify() (existing jsonl / smtp / telegram / webhook backends)

web (edge) ──► api /hub/api/v1/monitoring/* ──► ClickHouse
```

### Wire protocol v1

- **Datagram:** one UTF-8 JSON object per datagram, at most **1200 bytes**, so it never
  fragments. A sender that goes over the limit shortens `message` first, then drops
  `context`.
- **Common fields:**

  | Field | Type | Meaning |
  |---|---|---|
  | `v` | int | Protocol version, `1` |
  | `type` | `hb` \| `warn` \| `err` | Packet kind |
  | `id` | string | Stable service id: the compose service name (`ingest-us`), or `strategy-<code>` matching `book.strategies` |
  | `inst` | uuid4 | Generated once per process start; a new value means a restart |
  | `seq` | uint | Rises by one per packet within a process; gaps mean lost packets |
  | `ts` | int | The sender's UTC clock, in epoch milliseconds |
  | `host` | string | Host or container name |

- **Heartbeat (`hb`) fields:**
  - `interval_s`: how often this sender heartbeats. The monitor derives its deadline from it.
  - `state`: `starting` \| `running` \| `idle` \| `stopping`. `stopping` tells the monitor the
    stop is deliberate, not a death.
  - `uptime_s` (optional).
- **Warning and error (`warn` / `err`) fields:**
  - `error_type`: a snake_case code. The starting set is the `ErrorKind` values from
    [errors.py](../../libs/ingest/src/factorlab/ingest/errors.py) (`auth`, `rate_limited`,
    `transient`, `permanent`, `normalization`, `other`) plus `crash`.
  - `message`: at most 512 characters.
  - `severity`: `err` packets only, `error` \| `fatal`.
  - `context` (optional): a flat dict.
  - `fingerprint` (optional): a dedupe key.
- **Versioning:** the receiver ignores fields it does not know. It drops a packet with an
  unknown `v` or `type` and counts it.
- **Time:** liveness always uses the monitor's receive time, never `ts`. Clock skew above
  5 s raises a warning that the monitor generates itself.

```json
{"v":1,"type":"hb","id":"ingest-us","inst":"6f1c…","seq":812,"ts":1790000000000,"host":"ingest-us","interval_s":30,"state":"running","uptime_s":24360}
{"v":1,"type":"err","id":"ingest-india","inst":"a41e…","seq":90,"ts":1790000000000,"host":"ingest-india","error_type":"auth","severity":"error","message":"Upstox token rejected (401)","context":{"provider":"upstox"}}
```

### Sender client (`libs/runtime`)

- **API:** a new `factorlab.runtime.monitor` module with
  `MonitorClient(id).heartbeat(state=…)`, `.warn(error_type, message, **ctx)` and
  `.error(error_type, message, severity=…, **ctx)`.
- **Dependencies:** stdlib only (socket and json), so runtime still depends only on core.
- **Behaviour:** fire-and-forget. The socket is non-blocking and the client never raises,
  so it cannot slow or crash the loop that calls it.
- **Configuration:** `FACTORLAB_MONITOR_ADDR` (for example `monitor:9125`) turns the client
  on; if it is empty, the client does nothing. The id defaults to the existing
  `FACTORLAB_SERVICE` env var.
- **Integration points:**
  - `Heartbeat.tick()` also sends an `hb`, rate-limited to `interval_s`. The file heartbeat
    stays for the Docker healthcheck.
  - `supervised()` sends `err` / `crash` / `fatal` from its crash path, and
    `state=stopping` on a clean exit.
  - The ingest engine turns unit failures into `warn` or `err`, using `classify()` to get
    `error_type`.
- **Conformance:** golden packet fixtures in `libs/runtime/tests/fixtures/monitor/`. Senders
  in other languages (strategies, trade engine) test against the same files.

### Monitor component (`components/monitor`, new)

- **Scaffolding:** created with `tools/scaffold.py` (the `new-component` skill).
  - Package `factorlab-component-monitor`, entrypoint `factorlab-monitor`, one
    `mode: daemon` service called `monitor`.
  - Health: `{kind: http}` on a local `/healthz`. Secrets: `[CLICKHOUSE_PASSWORD]`.
  - Rollback class `writer` (ADR-0015). Logs go to `/var/log/factorlab/monitor` (ADR-0014).
- **Compose fragment:** joins only `backend`, with `expose: ["9125/udp"]` and no host port.
  Runs `read_only` with a small tmpfs, uses the standard `x-logging` anchor, and has a
  256 MB memory limit.
- **Receiver:** an asyncio datagram receiver. Invalid packets are counted, and a sample
  is logged.
- **Expected services:** each service in a `component.yaml` can carry an optional
  `monitor: {interval_s, max_silence_s, active_window}` block. `tools/components.py`
  validates it and renders `deploy/monitor-services.yaml` next to the compose file. Jobs
  such as `ingest-political` use `max_silence_s` (about 26 h) instead of `interval_s`. An
  id that sends packets but is missing from the file is tracked as `unregistered`, and the
  monitor warns once.
- **State per id:** `unknown → up → late (> 2×interval) → down (> 3×interval or
  max_silence) → up`. A `state=stopping` heartbeat moves the id to `stopped`. A new
  `inst` records a restart, and `seq` gaps count as packet loss.
- **Alerts** go through `notify()` and use its 5-minute dedupe:

  | Event | Severity |
  |---|---|
  | `down` | `fail` |
  | Recovered | `info` |
  | Unexpected restart (new `inst` with no preceding `stopping`) | `warn` |
  | `err` packet | `fail` / `fatal`, deduped on `(id, error_type, fingerprint)` |
  | `warn` packets | Stored and shown in the UI; alert only above N per M minutes |

- **Who watches the watcher:**
  - Every flush, the monitor writes its own `monitor` row to `meta.service_status`.
  - The API and UI flag that row when it goes stale, and there is a compose healthcheck.
  - An `uptime-kuma` push monitor is optional.

### Storage (`libs/schema`, wave 11)

- **Migration:** `wave_11_schema_service_monitoring.sql` under
  `libs/schema/src/factorlab/schema/sql/clickhouse/v2/`, with `checksums.lock` updated
  through `tools/schema_checksums.py`.
- **`meta.service_events`** (MergeTree, `PARTITION BY toYYYYMM(received_at)`,
  `ORDER BY (service_id, received_at)`):
  - Columns: `received_at`, `sent_at`, `service_id`, `instance_id`, `seq`, `type`,
    `origin` (`sender` \| `monitor`; state changes are monitor-generated events), `state`,
    `severity`, `error_type`, `message`, `context_json`, `host`, `skew_ms`.
  - TTL: `hb` rows 14 days, everything else 180 days.
  - Expected volume is about 45k rows a day (about 15 services × one heartbeat per 30 s).
- **`meta.service_status`** (ReplacingMergeTree(version)), one current row per service:
  - Columns: `status`, `last_seen_at`, `instance_id`, `instance_started_at`,
    `restarts_24h`, `last_error_at`, `last_error_type`, `last_error_message`,
    `warn_count_1h`, `err_count_1h`, `updated_at`.
- **Column types:** v2 conventions (`DateTime64(3, 'UTC')`, `LowCardinality(String)`).
- **Access:** a `monitor` ClickHouse user that can only INSERT and SELECT `meta.service_*`.
- **Write path:** inserts are batched every 5 s or 500 rows. If ClickHouse is down, the
  monitor keeps a bounded in-memory buffer, spills to JSONL under its log directory, and
  replays when ClickHouse recovers. Alerting never depends on ClickHouse.

### Platform, API and web

- **Platform:** a `monitor_runtime` tmpfs volume in `deploy/compose.base.yml`. The secrets
  agent writes `CLICKHOUSE_PASSWORD` into it, and senders get `FACTORLAB_MONITOR_ADDR`.
- **API:** a new `monitoring.py` module in `components/api` that reads ClickHouse, so the
  page still works while the monitor is down:
  - `GET /hub/api/v1/monitoring/services`: status joined with the registry.
  - `GET /hub/api/v1/monitoring/events?service_id=&type=&since=&limit=`
- **Web:** `components/web/src/pages/Monitoring.tsx` and a test, linked from
  `operationsLinks` in `App.tsx`. It polls every 15 s and shows:
  - a status grid, one tile per service coloured by status, with last-seen age, uptime
    and restarts;
  - a filterable events table;
  - a "monitor offline" banner when the monitor's own row is stale.

  The web container stays on `edge` only and gets everything through the API.

## Non-goals

- Metrics, histograms, tracing, Prometheus or log shipping.
- Packet authentication (for example HMAC). The private `factorlab-backend` network is the
  trust boundary. Revisit this if a sender ever runs off the VPS, such as a laptop over
  Tailscale.
- Replacing F-026. F-026 alerts on failed runs and ERROR logs. This feature covers
  liveness and errors that services report about themselves. Both go through `notify()`.
- Guaranteed delivery. UDP is best-effort, and the 3× deadline absorbs occasional loss.
- Retiring the file-mtime heartbeat, which the Docker healthcheck still uses.

## Acceptance criteria

- [ ] `docker kill` of a daemon marks it `down` and sends a `fail` alert within 3× its
      `interval_s`.
- [ ] `docker compose stop` of a daemon shows it as `stopped` and sends no alert.
- [ ] A process restart (new `inst`) is recorded and sends a `warn` alert.
- [ ] A `MonitorClient.error()` call is in `meta.service_events` and on the hub page
      within 10 s.
- [ ] With the monitor stopped, senders run with no errors and no measurable added latency.
- [x] With ClickHouse down, alerts still fire, and the buffered events are written once it
      recovers.
- [x] The golden packet fixtures pass in both the runtime and the monitor tests.
- [ ] The hub page shows a "monitor offline" banner when the monitor's own status row is
      stale.

## Rollout

Everything waits for F-007 to reach per-component releases.

1. A `runtime` release with the client. It does nothing until `FACTORLAB_MONITOR_ADDR`
   is set.
2. The `schema` wave 11 migration, and the `platform` changes: the `monitor_runtime`
   volume, secret delivery, and the `monitor:` manifest block with its registry render.
3. The first `monitor` component release.
4. `api` and `web` releases with the monitoring page.
5. Each sender component turns the client on in its next release by setting
   `FACTORLAB_MONITOR_ADDR=monitor:9125`.

Open questions:
- Should strategies get a tighter deadline (seconds) than ingestors?
- Should the Phase 9 kill switch read `meta.service_status`?
- How should `active_window` treat services that only matter during market hours?
- Does the protocol choice deserve its own ADR once the design is accepted?

## Log

- 2026-09-30: deployed the owner-authorized VPS branch trial from `60afea8` after
  fixing lost initial DNS heartbeats and adding completed-work progress reporting.
  Wave 11 is applied; monitor/API/web, runtime secrets, sender configuration and
  log rotation are installed. Health, received heartbeats, public monitoring assets,
  API filters and existing API/ClickHouse preservation passed. Political awaits its
  next scheduled run; normal releases and remaining production acceptance exercises
  are still pending. See the [rollout record](../operations/service-monitoring.md).

- 2026-09-30: implemented runtime sender/lifecycle hooks, production daemon waits,
  monitor receiver and state machine, bounded queues and JSONL replay, wave 11,
  registry/platform/secret wiring, monitoring API and hub page. Local ClickHouse 25.3
  Docker verification passed for DDL, restricted grants, event storage, deliberate
  stops and outage alert/replay. Production rollout remains pending; see
  [service-monitoring.md](../operations/service-monitoring.md).

- 2026-09-30: created (backlog).
