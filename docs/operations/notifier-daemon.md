# Notifier daemon — `127.0.0.1:8765`

**Owner:** turing (build) · heimdall (security review) · ritchie (Task Scheduler entry) · **Last updated:** 2026-05-15

## What it does

Small Flask service that owns the Outlook COM handle on this Windows host. Any process — script, future container — that wants to send a notification calls `factorlab.shared.notify.notify(...)` which either:
1. Hits Outlook directly (host script, default backend `outlook`), or
2. POSTs to this daemon at `http://127.0.0.1:8765/alert` (container client, default backend `webhook`).

Always also appends to `LOG_ROOT/notify.jsonl` (canonical audit record — never lost even if Outlook is closed).

## Endpoints

| Method | Path        | Body / Headers                                                                 | Returns                                              |
|--------|-------------|--------------------------------------------------------------------------------|------------------------------------------------------|
| GET    | `/healthz`  | (none)                                                                          | `{"status":"ok","outlook_available":true|false,"last_send":"<UTC>"}` |
| POST   | `/alert`    | JSON body `{subject, body, severity, source, occurred_at?}` + `X-Notify-Pin` header | `{"ok":true,"results":{...}}`                        |

## Required env vars

```
FACTORLAB_NOTIFY_PIN=<random-secret>     # required — daemon refuses to start without it
FACTORLAB_NOTIFY_TO=adoshi@rvcapital.com # Outlook recipient
FACTORLAB_NOTIFY_DAEMON_HOST=127.0.0.1   # default; only localhost allowed
FACTORLAB_NOTIFY_DAEMON_PORT=8765        # default
```

`FACTORLAB_NOTIFY_TO` lives **only** in `.env` — never appears in source/logs (project rule).

## Launch

```powershell
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" scripts\_shared\notifier_daemon.py
```

Or via Task Scheduler at log-on (Phase 7 entry `FactorLab-NotifierDaemon` — see [`windows-task-scheduler.md`](windows-task-scheduler.md)).

## Verify it's up

```powershell
curl http://127.0.0.1:8765/healthz
```

Expect `{"status":"ok","outlook_available":true,"last_send":null}` on a fresh start.

## Send a test alert

```powershell
$env:FACTORLAB_NOTIFY_BACKEND="outlook"
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" -c @"
from factorlab.shared.notify import notify
notify('smoke test', 'body line\\nsecond line', severity='warn', source='manual', dedupe_key=False)
"@
```

Outlook draft appears + `logs/notify.jsonl` gets a line.

## Failure modes

| Symptom                                          | Cause                                              | Fix                                                                                  |
|--------------------------------------------------|----------------------------------------------------|--------------------------------------------------------------------------------------|
| Daemon refuses to start: "no PIN"                | `FACTORLAB_NOTIFY_PIN` unset                       | Set the env var (fail-closed by design)                                              |
| Daemon refuses to start: "non-localhost bind"    | `FACTORLAB_NOTIFY_DAEMON_HOST` was set to `0.0.0.0` | Leave it at default `127.0.0.1` — heimdall policy                                    |
| `/alert` returns 401                             | `X-Notify-Pin` header missing or wrong             | Verify caller's `FACTORLAB_NOTIFY_PIN` matches daemon's                              |
| `outlook_available: false` in healthz            | Outlook not installed / no user session            | Outlook must be open on the device; daemon can still write JSONL                     |
| Notifications duplicate                          | Caller passed `dedupe_key=False` repeatedly         | Use default dedupe (5-min window) or specify a `dedupe_key`                          |

## Heimdall sign-off

- ✅ `127.0.0.1`-only bind enforced at startup; non-localhost refused
- ✅ Shared `X-Notify-Pin` header required on `/alert`
- ✅ Fails closed without PIN
- ✅ Recipient address only in env, never in code/logs
- ✅ JSONL canonical record alongside primary backend
