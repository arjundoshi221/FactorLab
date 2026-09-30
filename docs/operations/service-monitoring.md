# Service monitoring

F-035 adds a UDP monitor on the private backend network. The hub Operations menu links
to `/monitoring`; its API reads ClickHouse independently of the monitor process.

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
