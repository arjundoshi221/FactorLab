# factorlab-runtime

> Daemon scaffolding shared by every component: exit codes, heartbeat, file lock, graceful shutdown, `supervised`, subcommand dispatch and notifications.

## Purpose

Process-level primitives for long-running and scheduled entry points. Every
component's console script is built on `runtime.cli.dispatch`, and the engine daemon
in `factorlab-orchestration` loops with `GracefulShutdown` and `Heartbeat`. It sits
one layer above core (`LIBRARY_LAYERS["runtime"] = {"core"}` in
[test_boundaries.py](../../tests/architecture/test_boundaries.py)).

## Owns and does not own

- Owns: `ExitCode` ([exit_codes.py](src/factorlab/runtime/exit_codes.py)),
  `Heartbeat` ([heartbeat.py](src/factorlab/runtime/heartbeat.py)), `acquire_lock`
  ([lock.py](src/factorlab/runtime/lock.py)), `GracefulShutdown`
  ([signals.py](src/factorlab/runtime/signals.py)), `supervised`
  ([supervised.py](src/factorlab/runtime/supervised.py)), `Command`/`dispatch`
  ([cli.py](src/factorlab/runtime/cli.py)) and `notify` with its backends
  ([notify/](src/factorlab/runtime/notify/__init__.py)).
- Does not own: heartbeat file locations and the `factorlab-healthcheck` probe
  (`factorlab.core.paths` / `factorlab.core.healthcheck`), logging
  (`factorlab.core.logging`), trading calendars (`factorlab-calendars`), the local
  notifier daemon the `webhook` backend posts to.

## Entry points

None (library). Public API:

- `ExitCode`: `OK=0`, `WARN=2`, `FATAL=3`, `NOT_TRADING_DAY=10`, `LOCK_HELD=75`,
  `CRASH=99`, plus the `EXIT_*` int aliases.
- `dispatch(prog, commands, argv)`: runs `commands[argv[0]]` with the rest; help or no
  arguments prints usage (exit 0 for help, 2 for none); unknown command exits 2.
- `Heartbeat(service).tick()`, `acquire_lock(path, stale_seconds=12h)`,
  `GracefulShutdown(log)` (SIGINT/SIGTERM set `.triggered`),
  `supervised(main, name=..., on_crash=None, enable_heartbeat=True)`.
- `factorlab.runtime.notify.notify(subject, body, severity=, source=, ...)`.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Notify backends | `FACTORLAB_NOTIFY_BACKEND` | `outlook` | Comma list; `jsonl` is always appended |
| Recipient | `FACTORLAB_NOTIFY_TO` | unset | `outlook`, `smtp`; never logged |
| SMTP | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | `smtp.gmail.com`, `587`, unset, unset | `SMTP_PASSWORD` is a secret name |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | unset | `TELEGRAM_BOT_TOKEN` is a secret name |
| Webhook | `FACTORLAB_NOTIFY_URL`, `FACTORLAB_NOTIFY_PIN` | `http://127.0.0.1:8765/alert`, unset | See [notifier-daemon.md](../../docs/operations/notifier-daemon.md) |
| Host label | `COMPUTERNAME` | `socket.gethostname()` | Stamped on each `Notification` |
| Heartbeat / log roots | `FACTORLAB_HEARTBEAT_ROOT`, `FACTORLAB_LOG_ROOT` | via `factorlab.core.paths` | |

The notify variables are read with `os.environ` at send time, not via `get_secret`,
so they cannot come from the secret volume. These reads are ratcheted in
[env_access_allowlist.txt](../../tests/architecture/env_access_allowlist.txt) (N4):
they may only shrink.

## Data

No ClickHouse access. Files: heartbeat files `<FACTORLAB_HEARTBEAT_ROOT>/<service>`
(liveness by mtime), lock files (JSON `{"pid", "started_at"}`, stolen after
`stale_seconds`), and the always-on audit log `<FACTORLAB_LOG_ROOT>/notify.jsonl`.

## Dependencies and contracts

- Workspace: `factorlab-core`. Third party: `requests>=2.32.3`; optional `outlook`
  extra `pywin32>=308` (Windows only).
- Exit codes are a contract with schedulers and compose health (`exit-zero`
  components) and with `orchestration.cli._exit` (`WARN` for partial, `FATAL` when
  every run failed).
- Heartbeat names are frozen once a manifest checks them, e.g. `ingest-broker`'s
  `health: {kind: heartbeat, service: ibkr_broker_snapshot, max_age: 300}`.
- `notify` dedupes `(source, severity, subject)` for 5 minutes; `fatal` always sends.

## Observability

Logs through the standard `logging` module, so records carry the JSON fields and
`run_id` bound by `factorlab.core.logging`. `supervised` logs the traceback of an
uncaught exception and returns `CRASH`. Heartbeats are probed with
`factorlab-healthcheck heartbeat <service> --max-age N`. Read production lines with
the [factorlab-logs](../../.claude/skills/factorlab-logs/SKILL.md) skill.

## Tests

`uv run pytest libs/runtime` (exit codes, signals, lock, heartbeat, supervised,
dispatch, notify backends and dedupe; `MarketWindow` from calendars is also tested
here).

## Release and rollback

Never released alone. Components whose closure includes it
([affected.py](../../tools/affected.py)): `ingest-broker`, `ingest-india`,
`ingest-political`, `ingest-us` and `schema-migrator`. Roll back by redeploying their
previous tags; the library version is not tagged.

## Pitfalls

- The default backend `outlook` needs `pywin32`; in Linux containers it logs
  "outlook backend unavailable" and only `jsonl` fires. Set `FACTORLAB_NOTIFY_BACKEND`.
- `acquire_lock` raises `SystemExit` (message only) when held; callers must map it
  to `ExitCode.LOCK_HELD` themselves.
- `Heartbeat.tick()` swallows `OSError`; a read-only heartbeat root fails silently
  and only the health probe notices.
- `supervised` re-raises `SystemExit`/`KeyboardInterrupt`; it only converts other
  exceptions to `CRASH`.
