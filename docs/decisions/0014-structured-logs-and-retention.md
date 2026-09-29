---
id: ADR-0014
title: Structured JSON logs with host-side retention and a read-only reader
status: accepted
date: 2026-09-29
status_note: Built on restructure/platform-v3; the host side (logrotate, journald, the factorlab-logs account) is installed by the first platform release, step R2 of feature F-007.
status_confirmed: false
---
# ADR-0014 — Structured JSON logs with host-side retention and a read-only reader

## Context

- Services reported through status prints and ad-hoc text logging (the India daemon
  configured logging at import time). Reading any of it on the VPS meant `docker logs`, which
  requires Docker access, and that is root-equivalent. The text was hard to filter by
  pipeline or run, and it disappeared when a release recreated the container.
- Nothing bounded disk use across Docker's json-file logs, journald and application files
  on a small VPS whose root disk also holds ClickHouse.
- Debugging needs to answer "what did this ingestion run do?", joining a
  `meta.ingestion_runs` row to its log lines, without granting shell or Docker access to
  whoever (or whatever agent) is reading.

## Decision

- **One logging module.** [`factorlab.core.logging`](../../libs/core/src/factorlab/core/logging.py)
  (stdlib only) is configured once at process start. Every record becomes one JSON object per
  line on stderr, so Docker captures it and stdout stays free for a command's own output.
  When `FACTORLAB_LOG_DIR` is set, the same records also go to
  `/var/log/factorlab/<component>/<service>.jsonl`.
- **Fields.** Each record carries `ts` (UTC, milliseconds), `level`, `component`, `service`,
  `version`, `commit`, `logger`, `msg` and `exc`. It also carries any `extra=` fields and
  whatever `log_context` binds: `run_id`, `pipeline`, `source`, `request_id`. Images set
  `FACTORLAB_COMPONENT`, and compose sets `FACTORLAB_SERVICE`.
- **Redaction at the writer.** Values under keys that look like credentials (password,
  secret, token, API key, authorization, cookie) become `[redacted]`. Bearer tokens and
  `token=`/`api_key=`-style strings in free text are masked. Messages are capped at 16 KiB and
  exceptions at 32 KiB.
- **Logging never stops ingestion.** If the file sink cannot be opened, the process keeps
  logging to stderr and says so once.
- **Rotation without signals.** The file sink uses `WatchedFileHandler`, which reopens the file
  after logrotate renames it. [`deploy/logrotate/factorlab`](../../deploy/logrotate/factorlab)
  rotates daily or at 256 MB, whichever comes first, with an hourly timer
  (`deploy/systemd/logrotate.timer.d`). Files are compressed, and `maxage 30` enforces 30 days
  of retention. New files are `0640 factorlab:factorlab-logs`. The exception is `web`:
  nginx keeps its files open and the host has no way to signal it, so web uses
  `copytruncate` and accepts that it can lose a few access-log lines.
- **Backstops.** Every compose fragment has a json-file `logging` block (20 MB × 5,
  compressed). The fragment lint and the host deployer refuse a service without one.
  journald is capped at 1 GB (keeping 5 GB free) and 30 days
  (`deploy/systemd/journald.conf.d/60-factorlab.conf`).
- **Ownership.** Log directories are `<container uid>:factorlab-logs 2750`. The deployer
  resets them on every release.
- **A read-only reader.** The `factorlab-logs` account (UID 10002) belongs only to the
  `factorlab-logs` group, with no docker group and no sudo.
  [`deploy/ssh/60-factorlab-logs.conf`](../../deploy/ssh/60-factorlab-logs.conf) accepts only
  public keys from a root-owned file, gives no TTY, forwarding, tunnel or user rc, and forces
  one command: [`deploy/host/factorlab_log_reader.py`](../../deploy/host/factorlab_log_reader.py),
  installed as `/usr/local/bin/factorlab-log-reader`. The reader supports `list`, `tail`,
  `errors` and `run --run-id`. It reads only under `/var/log/factorlab` and the image
  snapshot, allows windows of at most 30 days, returns at most 200 records and 64 KiB, and
  redacts again. `prepare-host.sh` checks sshd with `sshd -t`, and removes the drop-in again if
  the check fails.
- **Clients.** [`tools/read_logs.py`](../../tools/read_logs.py) calls the reader over SSH. It
  never relaxes host-key checking, it redacts again and caps its output (12 KiB by default),
  and `--local DIR` reads fixtures instead. The `factorlab-logs` Claude skill
  (`.claude/skills/factorlab-logs`) wraps the client for live reads, used only when the user
  asks.

## Consequences

- One run's lines can be pulled by `run_id` across services, and errors can be grouped per
  component, without Docker or shell access to the VPS.
- Logs survive container recreation and releases. Disk use is bounded at every layer: app
  files, Docker's json-file logs and journald.
- An agent or a second person can read production logs with a key that can do nothing else.
  Revoking that access means removing one line from a root-owned file.
- Redaction happens three times: at the writer, in the reader and in the client. The reader
  keeps its own copy of the patterns, and `tests/deploy/test_log_reader.py` checks that the
  two copies stay in step.
- Adding a component means adding its directory to exactly one logrotate stanza, and
  `tests/test_deploy_host.py` checks this against the manifests.
- Retention is 30 days and there is no off-host copy. An incident older than that, or the
  loss of the VPS, loses its logs.
- ClickHouse's own server logging is outside this scheme until its maintenance window
  (rollout R5, feature F-007).

## Alternatives considered

- **Central log shipping** (Loki, Elasticsearch or a hosted service). Rejected for now: it
  means another stateful service or an egress path for a single small VPS, and the reader
  covers the questions actually being asked.
- **`docker logs` only.** Rejected: reading needs Docker access (root-equivalent), and the
  logs disappear when a container is recreated.
- **`copytruncate` for everything.** Rejected: it can drop lines between the copy and the
  truncate. It is used only for nginx, which cannot be signalled.
- **The journald Docker log driver.** Rejected: logs from every container share one journal
  with the host, and per-component retention and group-readable files are harder.
- **A sudo rule for reading logs.** Rejected: any sudo rule is a larger grant than a
  ForceCommand that can only read.
