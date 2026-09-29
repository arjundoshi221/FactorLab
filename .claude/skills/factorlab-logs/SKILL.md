---
name: factorlab-logs
description: Read FactorLab production logs per component and service (tail, grouped errors, every line of one ingestion run) through the restricted factorlab-logs SSH account. Use when debugging a component on the VPS, a failed or rolled-back release, a stale pipeline, or a meta.ingestion_runs row; only when the user asks for a live read.
allowed-tools: Bash(uv run python tools/read_logs.py:*), Bash(python tools/read_logs.py:*)
---

# Reading FactorLab production logs

Every component writes JSON lines to `/var/log/factorlab/<component>/<service>.jsonl`
on the VPS. Logrotate keeps them hourly and size-capped for 30 days (compressed after
one rotation). `tools/read_logs.py` reads them over SSH as the `factorlab-logs`
account. sshd's ForceCommand lets that account run only
`deploy/host/factorlab_log_reader.py`, so this skill cannot change anything on the
host.

**Live reads only when the user asks** for production logs (AGENTS.md: no production
operations without an explicit request). For code questions, read the code instead.

## Components and services

| Component | Services (log files) |
|---|---|
| api | `api` (HTTP requests carry `route`, `status`, `duration_ms`, `cf_ray`) |
| web | `access` (nginx JSON access log), `error` (plain text) |
| secrets-agent | `cloudflare-secrets-agent` |
| schema-migrator | `bootstrap` |
| ingest-india | `ingest-india` |
| ingest-us | `ingest-us`, `universe-us` |
| ingest-political | `ingest-political`, `cron` (plain-text cron wrapper output) |
| ingest-broker | `ibkr-snapshot` |

Run `list` first when unsure. It shows the files present, their sizes and the running
containers with their versions.

## Commands

```bash
uv run python tools/read_logs.py list
uv run python tools/read_logs.py errors --since 24h                 # grouped, most frequent first
uv run python tools/read_logs.py errors --component ingest-us --since 6h
uv run python tools/read_logs.py tail --component ingest-us --since 2h --level WARNING
uv run python tools/read_logs.py tail --component api --grep "/hub/api/v1/overview" --lines 20
uv run python tools/read_logs.py tail --component ingest-india --regex "429|rate limit" --since 1d
uv run python tools/read_logs.py tail --component ingest-us --level ERROR --exc --lines 5
uv run python tools/read_logs.py run --run-id <uuid>                # one run, all components
```

- Records print as `ts LEVEL component/service logger: msg  [context]`. Tracebacks
  are summarized as `-> ExceptionType: message`; add `--exc` for the full traceback.
- `--json` prints raw JSON Lines when you need exact fields.
- The limits are enforced on the host: windows up to 30 days (`30m`, `2h`, `7d` or
  ISO 8601), at most 200 records, and 12 KiB printed by default. Narrow with
  `--since`, `--service`, `--level` or `--grep` rather than raising `--max-bytes`.
- `run_id` is the `meta.ingestion_runs.run_id` of the run. To cross-check what a run
  wrote, use the `factorlab-clickhouse` skill.

## A debugging routine

1. `errors --since 24h` gives the failure fingerprints and how often each occurs.
2. For the top fingerprint, run `tail --component <c> --level WARNING --since <around
   last>`. That shows the lead-up. Add `--exc --lines 3` to see the traceback.
3. If the record has a `run_id`, `run --run-id <id>` shows the whole run.
4. Explain what the logs show and what they do not. Propose a fix in code; do not
   try to operate on the VPS.

## Rules

- Output is already redacted, on the host and again in the client. Still, never
  copy credential-looking strings into files, commits or messages.
- Do not attempt other SSH commands, other accounts or port forwarding. The
  account refuses them, and trying them is out of scope.
- Setup, and when a read fails, see `docs/operations/log-access.md`. The failure is
  usually a missing key or `known_hosts` entry, or `FACTORLAB_LOGS_SSH_HOST` is not set.
