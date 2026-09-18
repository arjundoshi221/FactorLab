# Windows Task Scheduler registry

**Owner:** ritchie · **Last updated:** 2026-06-16

> This file is a **tracking registry** of scheduled jobs we intend to run.
> It does not drive setup — registration in Windows Task Scheduler happens
> manually in prod by the owner. Treat the table below as the source of truth
> for *what should exist*; current run state is tracked in the "Current state"
> section.

## Authoritative job list

| Task name                          | Trigger              | Action                                                                                                            | Owner script                                | Notes                                                                                              |
|------------------------------------|----------------------|-------------------------------------------------------------------------------------------------------------------|---------------------------------------------|----------------------------------------------------------------------------------------------------|
| `FactorLab-NotifierDaemon`         | At log-on            | `python scripts\_shared\notifier_daemon.py`                                                                       | [`notifier-daemon.md`](notifier-daemon.md)  | Must be up before any live poller fires                                                            |
| `FactorLab-IndiaEquities-Upstox-PreMarket` | Daily 06:00 IST | `python scripts\in\equities\upstox\india_equities_upstox_premarket.py`                                            | [`live-services.md`](live-services.md)      | Sync Upstox token + refresh instruments before market open                                         |
| `FactorLab-IndiaEquities-Upstox-Live` | Daily 03:40 UTC    | `python scripts\in\equities\upstox\india_equities_upstox_live.py --universe nifty500 --daemon`                    | [`live-services.md`](live-services.md)      | Long-running; exits ~10:05 UTC. Use `--daemon` to keep alive across days.                          |
| `FactorLab-USEquities-Schwab-Live` | Every hour at :05    | `python scripts\us\equities\schwab\us_equities_schwab_live.py`                                                    | [`live-services.md`](live-services.md)      | 24/7 cron. Pulls last 2h of 1m S&P 500. Off-hours = cheap no-ops. Idempotent upsert.               |
| `FactorLab-USEquities-Schwab-EOD`  | Daily 17:30 ET       | `python scripts\us\equities\schwab\us_equities_schwab_eod.py`                                                     | [`live-services.md`](live-services.md)      | Pulls last 7d of daily R3K. Catches up missed runs automatically.                                  |
| `FactorLab-USEquities-EODHD-Daily` | Daily 16:30 ET       | `python scripts\us\equities\eodhd\us_equities_eodhd_daily.py`                                                     | [`live-services.md`](live-services.md)      | EODHD EOD bars                                                                                     |
| `FactorLab-USPolitical-Daily`      | Mon–Sat 17:30 IST    | `python scripts\us\political\us_political_daily.py --mode daily`                                                  | [`live-services.md`](live-services.md)      | 7-day rolling; includes Senate eFD (host-only)                                                     |
| `FactorLab-USPolitical-Weekly`     | Sun 17:30 IST        | `python scripts\us\political\us_political_daily.py --mode weekly`                                                 | [`live-services.md`](live-services.md)      | Daily + USASpending + Finnhub + FEC + anomaly snapshot                                              |
| `FactorLab-USPolitical-Hourly`     | Every hour 00:15     | `python scripts\us\political\us_political_daily.py --mode hourly`                                                 | [`live-services.md`](live-services.md)      | Self-healing retry; skips sources still in cooldown                                                |
| `FactorLab-USEquities-Schwab-AuthRefresh` | Weekly Sun 02:00 ET | `python scripts\us\equities\schwab\us_equities_schwab_auth.py --refresh`                                         | [`live-services.md`](live-services.md)      | Refresh token expires every 7 days; this preempts                                                  |
| `FactorLab-NAS-Sync-Weekly`        | Sun 03:00            | `python scripts\_shared\sync_raw_to_nas.py --dest E:\NAS\factorlab\raw --sync`                                    | [`nas-storage.md`](nas-storage.md)          | Additive backup mirror                                                                              |
| `FactorLab-NAS-Verify-Weekly`      | Sun 04:00            | `python scripts\_shared\sync_raw_to_nas.py --dest E:\NAS\factorlab\raw --verify`                                  | [`nas-storage.md`](nas-storage.md)          | sha256 1% sample; non-zero exit fires `notify(severity='warn')`                                    |

## Current state (2026-06-16)

**Nothing is currently scheduled or running in Windows Task Scheduler.**

The table above is the target list. When jobs are registered in prod, this
section should be updated to reflect what's actually live.

Suggested re-enable order when bringing things back up:

1. `FactorLab-NotifierDaemon` first (everything else's `on_crash` depends on it).
2. `FactorLab-IndiaEquities-*` next (oldest production surface, well-soaked).
3. `FactorLab-USPolitical-*` after.
4. `FactorLab-USEquities-*` last.
5. `FactorLab-NAS-*` as the final hardening.

## Built-in observability (when jobs are running)

Each script handles its own runtime instrumentation via
`factorlab.shared.runtime.supervised(...)` (heartbeat updates,
exception-to-`notify(fatal)` routing) and `setup_logging(...)` (dated log
files + stdout). No `.bat` wrappers needed.

- **Heartbeat:** `data/_heartbeat/<service>` mtime, updated every loop tick by `factorlab.shared.runtime.Heartbeat`.
- **Per-script log:** `logs/<script-name>_YYYYMMDD.log`, written by `setup_logging(...)`.
- **Notify JSONL:** `logs/notify.jsonl` — every alert ever fired.
- **Per-mode bundle (political):** `logs/political_<mode>_YYYYMMDD/orchestrator.log`.
- **Run state (political):** `logs/political_run_state.json` — per-source cooldown gate.
- **Anomaly snapshot (political weekly):** `logs/political_metrics.jsonl` + `logs/political_alerts.jsonl`.

## Sister docs

- [`live-services.md`](live-services.md) — what each task does
- [`notifier-daemon.md`](notifier-daemon.md) — the daemon that must be up first
- [`senate-efd-host.md`](senate-efd-host.md) — why political-daily can't move off this host
- `E:\AGENTS\memory\projects\factorlab\docs\scheduled-jobs.md` — same content as above in Nandi memory
