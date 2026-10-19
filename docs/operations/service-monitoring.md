# Service monitoring

F-035 adds a UDP monitor on the private backend network. The hub primary navigation links
to `/monitoring`; its API reads ClickHouse independently of the monitor process.

## Hub page

The page opens with a **status verdict** (F-038) from `/hub/api/v1/monitoring/summary`:
"All systems working", "N things need attention", "Some checks could not run" or
"System status unavailable". Problems appear worst first, each with a link to its
evidence: a service, pipeline or coverage card on this page, the data matrix filtered to
tickers missing their latest session, a broker account, or the trade ledger. Notes,
such as feeds without session checks and untagged orders, never change the verdict.
Passing checks are collapsed. The summary rolls up services, source status, pipelines,
coverage feeds, broker snapshots of active non-simulated accounts and ticker freshness.
Each area is read independently, so a failed read shows as "not checked" and never as
healthy. The result is cached for 15 seconds. The section navigation stays visible
while scrolling.

Service cards put delayed/missing heartbeats first and distinguish
scheduled jobs awaiting their first monitored run from services missing a heartbeat.
Null report ages display "Not yet reported"; unavailable instance uptime displays a dash.
Select a card to filter its events, or use the service, event type and time range controls.
The log shows the latest 100 matching events, with timestamps in the browser's local
timezone. Clear filters returns to the full event history.

The page refreshes every 15 seconds and supports manual refresh. Loading, empty history,
an offline monitor and failed API requests have separate messages. Failed refreshes
retain the previous successful snapshot and its checked time; changing filters hides
the preceding filter's events until a matching response arrives.

## Operations grid

Service cards include errors/warnings in the last hour, packet gaps and configured
heartbeat or job deadlines. Search by name/ID or enable **Needs attention only** to
include late/down/unknown services, unregistered senders and recent errors/warnings.
An on-time heartbeat can coexist with ingestion errors. The seven-day strips count
stored reports, errors, warnings, restarts and down transitions by UTC calendar day; focus or hover a day
for its counts. Grey means no stored reports, not a healthy day. Green denotes reports
without error packets or down transitions, not continuous uptime. Daily error totals use all stored events,
independently of the latest 100 events and their filters.

**Data completeness** shows each feed's latest recorded session within 30 days: recorded
missing bars, affected series, checked series, expected bars and check timestamp. Active
non-contract listing series without a record for that feed's latest recorded session are
counted separately. A registered active feed with no checks still appears with no session
and unknown bar coverage. The bar percentage excludes unreported series. This view does
not infer exchange holidays or detect an entirely absent expected session; check the
displayed session date and pipeline schedule when interpreting coverage.

**Daily ingestion** reuses the catalog's schedule-aware pipeline health. Cards summarize
rolling 24-hour runs, failed/partial runs and rows, plus seven calendar days of UTC activity.
Expand recent problems to see run errors and failed series. **Problems only** includes
attention/missing pipeline statuses and any failed/partial run in the last 24 hours.
The catalog can cache these results for 60 seconds. Links open coverage records and the
pipeline explorer for investigation.

Each operations panel refreshes independently every 15 seconds, and **Refresh now**
refreshes all panels. A failed panel retains a marked stale snapshot without preventing
the other panels from updating. The service grid marks heartbeat statuses stale when
the monitor is offline or the service/event refresh fails. No new schema migration is
required for these read APIs; deploy API and web together to expose the new panels.

## Error inbox and collection failures

Reported source problems appear in the status verdict. A universe source error marks
`universe-us` as needing attention even if the daemon's heartbeat is on time and its
last-hour error counter is zero. Staleness follows each source's schedule, and the API
decides it (`source_health.py`). Most sources are stale after ten minutes without a
report. IBKR (`ibkr-snapshot`) is stale only when a snapshot slot from at least one
hour ago has no newer snapshot. Its slots are `IBKR_SNAPSHOT_TIMES` on NYSE trading
days, and its normal `waiting` status between runs is not a problem. The same rule
drives portfolio snapshot freshness and the PnL stale-price flag. Use the **Error
inbox** link to investigate; process liveness does not establish successful collection.

The inbox reads `/hub/api/v1/monitoring/errors` independently of the latest 100 event
activity rows. Select the last 24 hours, 7 days (default), or 30 days, a service and a
severity. The API accepts `hours` (1-720), `service_id`, `severity` (`all`, `error`,
`fatal`, `warning`) and `limit` (1-200, default 100). It aggregates all matching stored
error/warning packets and failed/partial ingestion runs before applying the group
limit. Service filtering maps `us_universe_sync` to `universe-us` and the
`ibkr_broker_snapshot`/`ibkr_broker_simulation` pipelines and the `ibkr` source to
`ibkr-snapshot`. Current source
problems appear even if their last report predates the selected history window.

Expand a group for its message, first/last occurrence within the window, count, latest
affected run ID or host/instance, failed-series count across those runs, and recovery
evidence. Counts can represent repeated attempts for the same symbol, not distinct
affected symbols. Matching service telemetry and ingestion runs remain separate
evidence and must not be added as a single incident count. A later successful run of
the same pipeline and provider is labeled **Later successful run**; the inbox does not
assert that all missing records are repaired. Telemetry errors have unconfirmed recovery.
Source statuses have no invented occurrence count or first-failure timestamp.

The inbox displays at most the latest 100 groups; narrow the service/time filters if
truncated. Text search applies to loaded groups. Failed refreshes retain a marked stale
snapshot. Individual failed queries produce an **Incomplete error history** notice,
so missing results never imply a clear history. Query failures do not expose connection
details, and messages use the catalog's credential redaction. Reads are read-only,
bounded by the history window, with 5-second per-query deadlines and resource limits.

Caught universe daemon refresh failures now emit `universe_refresh_failed` error packets
with pipeline/provider/retry context; invalid configuration emits `universe_config_invalid`.
The retry wait preserves the redacted failure reason in source status. These error
packets use the monitor's existing notification and deduplication path. The one-shot
command also reports caught failures. UDP remains best effort, so the inbox additionally
reads persisted failed runs and source status, including historical failures from before
this instrumentation was deployed. Deploy `ingest-us`, API and web through their normal
release workflows to activate all changes; implementation alone does not deploy them.

## Rollout

Complete F-007's per-component release cutover first. Release runtime consumers with
sending disabled, apply pending schema wave 11 through the reviewed migration workflow,
then release platform, secrets-agent, monitor, API and web. These are separate operator
operations; implementing the feature does not run a migration or deploy anything.

For the explicitly authorized September 30 branch trial, use
`deploy/testing/monitor_test.py plan|apply|verify|restore <test-id>` on the VPS as
root after archiving tracked source and building its component images. This extends
the existing trial without modifying `main` or creating release tags. It preserves
provider settings, loopback ports and the existing Caddy route; it does not configure
Cloudflare Access or complete the normal release cutover. `apply` invokes the reviewed
migration runner with only wave 11 selected, adds the registry and monitor secret
volume, then replaces the agent, monitor, candidate API, web and heartbeat senders.
The political job uses the new image on its next scheduled invocation. Resolved
configuration backups stay root-only under `/opt/factorlab/deployment-tests/<test-id>`.
Failed startup or heartbeat verification restores preceding images; the additive
schema migration remains applied. `restore` uses the same pinned rollback model.
Verification requires current monitor, API, India, US and universe heartbeats, and
receipt from IBKR. Existing Gateway snapshot stalls can legitimately mark IBKR down
while its process remains running; the rollout does not hide these operational errors.

The platform installs generated `deploy/monitor-services.yaml` as
`/etc/factorlab/monitor-services.yaml`. Monitor and API mount this registry read-only.
Update manifests, run `uv run python tools/components.py render-compose`, and release
platform whenever expected services or deadlines change.

Set `FACTORLAB_MONITOR_ADDR=monitor:9125` in production environment configuration before
each sender's next release. Empty disables sending. UDP has no published host port;
web stays on edge and accesses monitoring through the API.

## Deadlines and schedules

A service's optional manifest `monitor` block accepts positive `interval_s` and/or
`max_silence_s`. Daemons become late at twice their interval and down at three times;
scheduled jobs use their maximum silence deadline even after a deliberate stop.
Profile-gated services remain unknown without alerts until their first packet.
Unregistered senders are tracked and generate one warning.

An optional `active_window` is explicit wall-clock configuration:

```yaml
monitor:
  interval_s: 30
  active_window:
    timezone: America/New_York
    days: [1, 2, 3, 4, 5] # ISO weekdays
    start: "09:30"
    end: "16:05"
```

Deadline evaluation pauses outside the window; opening it starts a fresh grace period.
Overnight windows belong to the weekday on which they start. This does not implement
exchange holiday calendars. Existing senders heartbeat during intentional idle waits,
so their manifests do not need market windows.

## Alerts and recovery

Down, recovery, restart and error alerts go through `notify()`. Warnings alert at ten
packets in five minutes. The monitor suppresses repeated alert keys for five minutes,
including fatal errors, and always stores the received error packets.
Configure existing notification backends through the monitor fragment's environment
variables; the default is JSONL under `/var/log/factorlab/monitor`.

The secrets agent gives the monitor only its ClickHouse password. The XML-defined
`monitor` user has SELECT and INSERT grants only on `meta.service_events` and
`meta.service_status`; it cannot run migrations or manage other users.

Storage batches every five seconds or 500 rows. Database writes and notifications run
away from the UDP event loop. During outages, pending events spill to
`/var/log/factorlab/monitor/spool/pending-*.jsonl` and replay on recovery. The spool sits
outside logrotate's JSONL glob. Memory is bounded to 10,000 queued events; if storage
and the spool both fail or prolonged writes fill the queue, old events can be lost.
An ambiguous database acknowledgement can replay a batch twice.

The monitor updates its own status at every flush and exposes local `/healthz` on 9126.
The API marks it offline after fifteen seconds without an update, or after a recorded
stop. If ClickHouse is unavailable, the page reports unavailable data and retains its
last successful snapshot.

## Verification

Run runtime and monitor tests against the shared golden datagrams, API tests, and
the web Monitoring test. Local Docker verification on ClickHouse 25.3 has exercised
wave 11 DDL, the restricted user, UDP errors, deliberate stops, notification delivery
with ClickHouse stopped, spool persistence and recovery replay. Production kill/stop
and release checks remain part of rollout.

## September 30 VPS branch trial

The owner authorized extending the existing trial while the normal release prerequisites
remain pending. Runtime images were built from tracked commit `60afea8`, version
`0.0.0-test.monitoring`, and applied under
`/opt/factorlab/deployment-tests/20260930-60afea8/` at approximately 14:40 UTC.
Wave 11 succeeded on the first attempt; that attempt restored previous images after
missing startup/progress heartbeats. A DNS-race regression test and completed-work
heartbeat hooks fixed the failure before the successful retry.

The runner verified the monitor, API, India, US, universe and IBKR receiving heartbeats;
all six were up at completion. API, web, monitor and secrets agent were healthy, and
the primary API and ClickHouse identities stayed unchanged. Public `/monitoring`, its
JavaScript bundle, both monitoring APIs, the new version and service/type filtering
passed HTTP checks. Political remains unknown until its next scheduled job. IBKR
can become late/down during the existing long Gateway snapshot operations; these
are reported by the monitor rather than hidden by background heartbeats.

`production.env` now enables `FACTORLAB_MONITOR_ADDR=monitor:9125`, and the monitor's
JSONL log rotation was added without replacing other operator rules. Their private
backups live beside the pinned rollback model; `monitor_test.py restore` restores
them when present. Alert delivery remains the local JSONL backend. Normal immutable
releases, Cloudflare Access setup and production crash/stop acceptance exercises
remain pending.

At 17:00 UTC, a web-only follow-up deployed tracked commit `379c377`, version
`0.0.0-test.monitoring-nav`, making Monitoring a visible primary navigation tab.
Thirteen navigation/page tests and the production build passed. The origin version,
served navigation bundle, web health and monitoring API passed verification; every
other running container remained unchanged. Rollback files are root-only under
`/opt/factorlab/deployment-tests/20260930-379c377/`. Public HTTP requests now reach
Cloudflare Access sign-in, so public page verification requires an authenticated session.
