# Orchestrators — Schedule, Phases, Failure Modes

> Status: `[live]` — operator's bird's-eye view across all FactorLab pipelines (US, India, Political). Last updated: 2026-06-16.

This is the operational map across every scheduled or long-running script in
`scripts/`. Use it to answer:

- **When does each script run?** (cadence, timezone, duration)
- **In what order should I run things on a fresh checkout?**
- **What happens on partial failure? How does it recover?**
- **Why are these split into multiple scripts instead of one monolith?**

For political-pipeline-specific operational detail (mode contract, exit
codes, FEC 429 walkthrough, anomaly thresholds), see
[`../data-sources/political/pipeline.md`](../data-sources/political/pipeline.md).
For market hours / sessions, see [`../countries/india-equities.md`](../countries/india-equities.md)
and [`../countries/us-equities.md`](../countries/us-equities.md). For vendor
auth lifecycles, see [`../data-sources/india/upstox.md`](../data-sources/india/upstox.md)
and [`../data-sources/us/schwab.md`](../data-sources/us/schwab.md).

## Temporal classes

Every script belongs to one of four operational classes. The class drives
how it's scheduled and how it fails.

| Class | Cadence | State | Examples |
|---|---|---|---|
| **Auth** | Human-initiated, infrequent | Token files on disk | `us_equities_schwab_auth.py`, `india_equities_upstox_auth_server.py` (Railway service) |
| **Backfill** | One-shot, heavy | None (idempotent upsert) | `us_equities_schwab_historical.py`, `us_political_backfill.py` |
| **Live daemon** | Long-running, market-hours only | Watermarks in DB | `india_equities_upstox_live.py` (US Schwab uses cron pattern instead — see below) |
| **Live cron** | Short-lived, fires every N minutes/hours | None (idempotent upsert, lookback window absorbs gaps) | `us_equities_schwab_live.py` (hourly 1m catch-up) |
| **Daily orchestrator** | Cron / Task Scheduler | Run-state JSON, file lock | `us_political_daily.py`, `us_equities_schwab_eod.py`, `us_equities_eodhd_daily.py` (stub today), `india_equities_upstox_premarket.py` |

A daily orchestrator is **not** a daemon. Daemons own the in-session loop;
daily orchestrators do post-close / pre-open one-shot work and exit.

The **Live daemon** vs **Live cron** split is a per-vendor design choice. India
Upstox's 2,000 req/30-min budget gives plenty of room for a 5-min daemon loop
across F&O-eligible names. Schwab's 2 req/sec safe rate makes per-minute
polling impractical for SP500-wide universe — an hourly cron with 2h lookback
gives equivalent freshness with far simpler ops.

---

## Schedule registry

| Script | Trigger | Timezone | Typical duration | Criticality |
|---|---|---|---|---|
| `us_equities_schwab_auth.py` | manual (weekly) | n/a | <1 min interactive | required for US live + backfill |
| `us_equities_blackrock_universe.py` | manual (quarterly) | n/a | ~1 min | feeds backfill + live |
| `us_equities_schwab_historical.py` | manual / on-demand | n/a | minutes-hours per frequency | one-shot per period |
| `us_equities_schwab_live.py` | Task Scheduler, hourly 24/7 | UTC | <10 min (498-symbol SP500 sweep) | session-critical |
| `us_equities_schwab_eod.py` | Task Scheduler, daily ~17:30 ET (post-close) | ET | ~10-30 min (2,548-symbol R3K) | daily |
| `us_equities_eodhd_daily.py` | **stub today** — EODHD fundamentals incremental still TBD | UTC | ~5 min (target) | daily |
| `india_equities_upstox_auth_server.py` | Railway deployment (24/7) | n/a | continuous | required for India auth |
| `india_equities_upstox_premarket.py` | Task Scheduler, daily 06:00 IST | IST | <2 min | session-critical |
| `india_equities_upstox_live.py` | manual start; respects XBOM calendar | UTC (03:40-10:05) | full session (~6h) | session-critical |
| `us_political_daily.py --mode daily` | Task Scheduler, Mon–Sat 17:30 IST | IST | ~5-15 min | daily |
| `us_political_daily.py --mode weekly` | Task Scheduler, Sun 17:30 IST | IST | ~30-60 min | weekly |
| `us_political_daily.py --mode hourly` | Task Scheduler, every hour 24/7 | IST | <5 s when nothing to do | self-healing |
| `us_political_backfill.py` | manual / phased | n/a | hours per phase | one-shot (or on-demand re-run) |

Notes:
- "Manual start" daemons (`india_equities_upstox_live.py`) expect an external
  supervisor (systemd, container, or just a screen session on the operator's
  machine). They are not in Task Scheduler because Task Scheduler is a poor
  fit for processes that need graceful SIGTERM and long-running watermark
  state.
- `us_equities_schwab_live.py` uses a cron pattern (not a daemon) — see
  [§Schwab live cron](#us_equities_schwab_livepy--hourly-1m-catch-up-cron) below
  for the rationale.
- `us_equities_eodhd_daily.py` is currently a 4-ticker EODHD demo. The Schwab
  daily ingest is now handled by `us_equities_schwab_eod.py`; the EODHD slot is
  reserved for fundamentals delta + corp-action refresh, not bars.

---

## US pipeline (Schwab + EODHD)

### `us_equities_schwab_auth.py` — OAuth bootstrap + 7-day re-auth
- **Why human-initiated**: Schwab requires browser MFA; cannot be
  automated headless.
- **Phases**: optional `--force` deletes cached token → `ensure_client(interactive=True)` →
  browser login → token cached → optional AAPL quote validation.
- **Cadence**: every 7 days (token refresh window). Run before Sunday's
  weekly heavy pulls.
- **Failure modes**: browser timeout, MFA failure, expired token →
  re-run with `--force`.

### `us_equities_blackrock_universe.py` — universe builder
- **Phases**: fetch IWV (Russell 3000) holdings CSV from BlackRock public URL →
  parse → filter Asset Class = Equity/Stock → sort by weight (mcap proxy) →
  optionally slice to top-N → write `configs/universes/us_<name>.yaml`.
- **Cadence**: quarterly (index reconstitution) or on demand.
- **No auth required** — public CSV.
- **Failure modes**: BlackRock format change → parse fails ("couldn't find
  holdings header row"); fix the parser. Network timeout → fatal exit 1.

### `us_equities_schwab_historical.py` — one-shot backfill
- **Phases**: load universe YAML → auto-seed missing instruments via Schwab
  quote metadata → fetch bars at requested frequency (1m / 5m / 10m / 15m /
  30m / 1d) → pool fetches across N workers (default 4) → write via
  `storage.ingest.write_candles`.
- **Cadence**: manual, one-off per frequency. Schwab's 1m max lookback is
  ~45 days; 5m is ~8 months; 1d is unlimited.
- **Failure modes**: bad universe → fatal. Symbol not in Schwab → warn, skip;
  partial backfill proceeds. Quota exhaustion → per-symbol fail; dedup at
  write means re-runs are safe.

### `us_equities_schwab_live.py` — hourly 1m catch-up cron
- **Run model**: short-lived cron job (NOT a long-running daemon). Each
  Task Scheduler firing is a self-contained pass.
- **Phases per firing**: load universe YAML → resolve instruments (auto-seed
  any missing via Schwab `/quotes`) → compute `[now-2h, now]` window →
  pool 1m-bar fetches across N workers (default 4) with 429/timeout retry →
  write via `storage.ingest.write_candles`.
- **Cadence**: every hour, 24/7. Outside US market hours (~08:00-00:00 UTC
  pre+regular+post) Schwab returns empty bars — cheap no-ops.
- **Why cron not daemon**: Schwab's 2 req/sec safe rate makes a single SP500
  pass take ~4 minutes — too long for per-minute polling. A daemon would
  add crash/restart complexity for no latency win. A 2h overlap window
  means missed runs are caught up automatically on the next firing
  within Schwab's 48-day 1m lookback.
- **Failure modes**: transient 429/timeout per-symbol → `_jobs.fetch_one`
  retries with exponential backoff (2s/4s/8s). Token expired → script
  exits non-zero, Task Scheduler restarts next hour; weekly `auth.py` cron
  preempts the 7-day expiry. Idempotent on the unique index — duplicate
  upserts are absorbed.

### `us_equities_schwab_eod.py` — daily R3K catch-up cron
- **Run model**: short-lived cron job, fires once per day after US close.
- **Phases per firing**: load R3K universe YAML → resolve instruments →
  compute `[now-7d, now]` window → pool 1d-bar fetches → write via
  `storage.ingest.write_candles`.
- **Cadence**: daily at 17:30 ET (30 min post-close to let Schwab settle).
- **Why 7-day overlap**: catches up any missed runs (weekend skips,
  Task Scheduler downtime). Idempotent upsert absorbs duplicates.
- **Failure modes**: same retry behavior as `live.py`. Token health is the
  main correlated risk — auth.py weekly cron must run.

### `us_equities_eodhd_daily.py` — STUB today (EODHD slot)
- **Current state**: `scripts/us/equities/eodhd/us_equities_eodhd_daily.py` is
  a 4-ticker demo (AAPL.US, TSLA.US, AMZN.US, VTI.US) that fetches daily
  bars and saves Parquet, with optional `--db` to write to Postgres.
- **Future role**: now that Schwab handles daily bars via `schwab_eod.py`,
  this slot is reserved for EODHD-only data: fundamentals deltas,
  corporate-action history, and other fields Schwab doesn't provide.
  Bars-via-EODHD is no longer the design target.

### Bring-up order (US)
1. `us_equities_schwab_auth.py` — once interactive, then weekly cron.
2. `us_equities_blackrock_universe.py` — once per quarter, writes
   `configs/universes/us_*.yaml`.
3. `us_equities_schwab_historical.py --frequency 1d` for the universe — fills
   `ref.instruments` and seeds `market_us.fact_equity` with full history.
4. `us_equities_schwab_historical.py --frequency 1m --universe sp500` — initial
   48-day 1m backfill, post which the hourly live cron maintains it.
5. Register `us_equities_schwab_live.py` (hourly cron) + `us_equities_schwab_eod.py`
   (daily post-close cron) in Task Scheduler. Both are idempotent and self-healing.
6. (Future) `us_equities_eodhd_daily.py` — EODHD fundamentals delta only;
   bars are now handled by Schwab.

---

## India pipeline (Upstox)

### `india_equities_upstox_auth_server.py` — Railway-hosted OAuth callback
- **Deployment**: 24/7 Flask app on Railway. Routes:
  `/login` (PIN-protected), `/callback` (Upstox redirect), `/token`
  (PIN-protected; local scripts fetch with `X-Auth-Pin` header),
  `/status`, `/health`.
- **Why on Railway, not local**: headless token refresh. Local cron scripts
  would otherwise need browser MFA daily. Auth server holds the token;
  scripts pull with PIN.
- **Security posture**: rate-limited (5 req/min), HSTS, X-Frame-Options
  DENY, timing-safe `hmac.compare_digest` on PIN. See
  [security audit](../security/audit-2026-05-08.md) for the cleared-on-review status.

### `india_equities_upstox_premarket.py` — daily 06:00 IST
- **Phases**:
  1. Trading-day check (XBOM calendar; `--force` overrides).
  2. Auth resolve (priority: local `.token` → Railway `/token` → interactive
     last resort).
  3. Validate token against Upstox profile endpoint.
  4. Refresh instruments master (NSE — currently the only enabled exchange).
  5. Build universes from instruments → write `data/in/universes/`.
  6. Email + Telegram alert on failure if configured.
- **Exit codes**: `0=success`, `1=auth fail`, `2=instruments fail`,
  `3=token validation fail`, `4=universe build fail`, `10=not a trading day`,
  `99=unexpected`.
- **Failure modes**: Upstox down → exit 1, alert. Holiday → exit 10
  (expected). Email/Telegram send fails → logged warning only.

### `india_equities_upstox_live.py` — long-running poller (03:40-10:05 UTC = 09:10-15:35 IST)
- **Phases**: ensure token → load universe → load instruments cache (from
  premarket) → DB sync (instruments + nearest-expiry F&O contracts) → loop:
  1. XBOM check; wait until 03:45 UTC.
  2. Every 5 min: equity 1-min bars for all symbols.
  3. Every 10 min (staggered): futures 1-min bars for F&O symbols.
  4. Watermark check.
  5. Write to `market_in.fact_equity_*` (canonical) + Arrow IPC cache
     (secondary, fast local replay).
  6. Every 30 min: token revalidate; re-auth if expired.
  7. On market close (10:00 UTC): final sweep + sleep until next pre-open
     (or exit if not `--daemon`).
- **Rate budget**: fo_eligible (~200 symbols) = 1,800 / 2,000 calls per
  30 min (90% utilization).
- **Why it can't be merged with premarket**: premarket is a one-shot
  pre-open prep job (sub-2-min). The 5min poller is a 6-hour daemon.
  Merging means either (a) the premarket job blocks for 6 hours, or
  (b) the polling loop redundantly does auth/instruments every iteration.
  This split was evaluated and rejected during the multi-agent audit
  (2026-05-08).

### Bring-up order (India)
1. Once: deploy Railway auth server, set `AUTH_SERVER_PIN`, do interactive
   browser login → token persists on Railway.
2. Daily 06:00 IST: `india_equities_upstox_premarket.py` (Task Scheduler).
3. Daily ~09:10 IST: `india_equities_upstox_live.py` started manually (or by an
   external supervisor — currently manual on the operator's machine).

### Task Scheduler registry

All India entries are registered directly via `schtasks` per
[`windows-task-scheduler.md`](windows-task-scheduler.md) — no `.bat`
wrappers. The two active task IDs are
`FactorLab-IndiaEquities-Upstox-PreMarket` (daily 06:00 IST, runs
`scripts/in/equities/upstox/india_equities_upstox_premarket.py`) and
`FactorLab-IndiaEquities-Upstox-Live` (daily 03:40 UTC, runs
`scripts/in/equities/upstox/india_equities_upstox_live.py --universe nifty500 --daemon`).
The legacy "Hourly" / "CloseSwoop" tasks from the old `.bat`-driven
flow are removed — the 5-min poller now handles the full session
internally, and any historical gap-fill belongs to the manual
`scripts/in/equities/upstox/india_equities_upstox_historical.py` one-shot.

---

## Political pipeline (8 sources)

For mode contract (daily / weekly / hourly / verify-only), exit codes,
anomaly thresholds, FEC 429 walkthrough, and Senate eFD historical-backfill
gotchas, see [`political-pipeline.md`](political-pipeline.md). This section
is just the orchestrator-level summary.

### `us_political_daily.py` — daily / weekly / hourly / verify-only
- **Concurrency**: file lock at `logs/.political.lock` (12h stale threshold;
  next run steals the lock if the holder is dead).
- **Sources**: legislators YAML, House Clerk PTRs, Senate eFD, LDA
  lobbying, USASpending contracts, Finnhub contracts, FEC, Congress.gov.
- **Fatality**: House Clerk failure is fatal (it's the spine — silent gaps
  in PTR coverage are unacceptable). All other sources are non-fatal:
  `senate_efd, lda, usaspending, finnhub_contracts, fec, congress_gov`.
- **Self-healing**: hourly mode consults `logs/political_run_state.json`.
  Sources that succeeded recently are skipped (success-cooldown). Sources
  that hit `RateLimitDeferred` (e.g. FEC 429 with Retry-After > 600s) are
  retried only after `retry_after_at` passes. See
  [hourly retry walkthrough](political-pipeline.md#how-the-hourly-retry-works).

### `us_political_backfill.py` — phased one-shot driver
- **Phases**: `--phase 1` (ref dims: legislators, committees, rules,
  bioguide) → `--phase 2` (trades: House Clerk + Senate eFD historical) →
  `--phase 3` (contracts + lobbying: USASpending FY range, Finnhub
  contracts, LDA year range, FEC cycles, Congress.gov congresses) →
  `--phase 4` (verify audit). `--all` runs them in order.
- **Configurable ranges**: `--house-clerk-years`, `--senate-efd-from/to`,
  `--contract-fy/fy-range`, `--lda-years`, `--fec-cycles`, `--congress`
  (repeatable).
- **Skip flags**: `--skip-senate-efd`, `--skip-usaspending`, `--skip-finnhub`,
  `--skip-lda`, `--skip-fec`, `--skip-congress`.
- **Senate eFD requires residential IP** (Akamai blocks cloud) — run from
  the operator's local Windows machine in a headed Playwright window.
  See [Senate eFD historical backfill](political-pipeline.md#senate-efd-historical-backfill-one-year-at-a-time).

### `_political_runner.py` — library helpers (not directly executable)
Shared across daily / weekly / hourly modes:
- `acquire_lock` (file-based mutex with 12h stale threshold)
- `RunState`, `should_run`, `mark_ok`, `mark_fail` (per-source cooldown gate)
- `Metrics`, `capture_metrics`, `compare_metrics` (week-over-week diff)
- `write_alerts`, `toast` (best-effort Win10 notification)
- `make_dated_log_dir`

This module is currently in `scripts/` but is library code, not an
entry point. It's queued for relocation to
`src/factorlab/sources/political/_runner.py` under Tier 1.

### Task Scheduler registrations
`scripts/setup_political_scheduler.bat` creates three tasks (all calling
`scripts/_run_political_task.bat <mode>` wrapper):

- `FactorLab-USPolitical-Daily` — Mon-Sat 17:30 IST
- `FactorLab-USPolitical-Weekly` — Sun 17:30 IST
- `FactorLab-USPolitical-Hourly` — every hour, 24/7

The wrapper exists because Task Scheduler defaults `cwd` to `System32` and
`conda run` fails silently under elevated tasks; the wrapper `cd`s to
project root, calls the env's `python.exe` directly, and tees stdout/stderr
to `logs/task_python_<mode>.{out,err}`. See
[Task Scheduler setup](political-pipeline.md#task-scheduler-setup).

---

## Failure mode quick reference

| Symptom | Likely cause | Recovery |
|---|---|---|
| Schwab live poller logs `401` mid-session | token expired | next 30-min revalidation auto-refreshes; if it doesn't, run `us_equities_schwab_auth.py --force` |
| India premarket exit 1 | Railway auth server down / token expired | check Railway service; re-do `/login` flow with PIN |
| India premarket exit 2 | Upstox instruments endpoint failed | retry — usually transient; cached instruments still serve the 5-min poller |
| India 5min: zero new bars for 5+ min during session | network blip / token expired / Upstox quota | check `data/upstox/instruments/` mtime; if stale, restart poller |
| Political daily exit 2 (warn) | `senate_efd` Akamai block, `lda` timeout, or other non-fatal source failed | normal on cloud / non-residential IP; hourly mode self-heals |
| Political daily exit 3 (fatal) | House Clerk schema mismatch / DB conn loss / OOM in PDF parser | escalate; fix root cause before next run |
| Political daily exit 75 | another orchestrator already running OR prior crash didn't release lock | check `logs/.political.lock`; if older than 12h, next run steals it |
| Political weekly anomaly: total_trades dropped | data loss | inspect `logs/political_alerts.jsonl` immediately |
| US daily stub returns "demo only" | running on the EODHD demo key | populate `EODHD_API_KEY` in `.env` from the real account |

For political triage, see the full triage table in
[`../data-sources/political/pipeline.md`](../data-sources/political/pipeline.md#triage-table--what-to-look-at-when-something-fires).

---

## Live-daemon runtime conventions

Long-running live daemons (`scripts/in/equities/upstox/india_equities_upstox_live.py`, the political hourly mode) import from `factorlab.shared.runtime`. The US Schwab pipeline uses a cron pattern (`us_equities_schwab_live.py` + `us_equities_schwab_eod.py`) instead, so it does NOT use these helpers — Task Scheduler provides the supervision externally. The conventions below apply to true daemons only.

The daemons use:

- `setup_logging(name)` — dated file + stdout, `LOG_ROOT/<name>_YYYYMMDD.log`
- `GracefulShutdown()` — SIGINT/SIGTERM → `.triggered` flag
- `WatermarkTracker()` — in-memory dedup (skip bars already written)
- `MarketWindow(calendar, open, close, tz, pre_open_min)` — `is_trading_day()`, `poll_start()`, `next_open()`
- `Heartbeat(service)` — touches `data/_heartbeat/<service>` each loop iteration
- `ExitCode` — canonical 0 / 2 / 3 / 10 / 75 / 99
- `supervised(main, name, on_crash)` — wraps top-level, catches everything, fires `notify(severity='fatal')` on crash

### Failure path

| What | Where it ends up |
|---|---|
| Per-symbol error inside `poll_once()` | Logged WARN; sweep continues |
| Token re-validation fails | Re-issue token; next poll cycle catches |
| Uncaught exception in `main()` | `supervised(...)` → `notify(severity='fatal', source=...)` + `ExitCode.CRASH` + log traceback |
| Heartbeat file stale (>5 min) | (Future) canary scheduled job pages an alert |
| Anomaly thresholds breach (political weekly snapshot) | `notify(severity='warn'\|'fail')` + JSONL alert |

### Verifying a live service is healthy

```powershell
# Is the heartbeat fresh?
Get-Item data\_heartbeat\india_equities_upstox_live | Select-Object Name, LastWriteTime

# What did the last sweep look like?
Get-Content (Get-ChildItem logs\india_equities_upstox_live_*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName -Tail 50

# Recent notifications
Get-Content logs\notify.jsonl -Tail 10
```

### Stop / restart

Each live daemon honours SIGINT / SIGTERM:

```powershell
# Find the python process
Get-Process python | Where-Object {$_.CommandLine -match "live\.py"}

# Stop gracefully (script flushes watermarks, exits at next loop tick)
$pid = (Get-Process python | Where-Object {$_.CommandLine -match "india_equities_upstox_live"}).Id
Stop-Process -Id $pid -Force:$false
```

Or Ctrl+C in the running terminal.

### What runs where

- **Windows host (this device):** all daemons (India live), all cron jobs (US Schwab live + EOD, political daily/weekly/hourly, NAS sync), and the notifier daemon. Task Scheduler is the supervisor for crons; daemons rely on graceful SIGTERM handling.
- **Railway:** Upstox OAuth-callback Flask service (`scripts/in/equities/upstox/india_equities_upstox_auth_server.py`) + Postgres + Timescale.

---

## US Daily — current state

The Schwab side of the daily orchestrator is now live:
`us_equities_schwab_eod.py` runs after the close and catches up the R3K daily
bars (7-day overlap for resilience). The EODHD slot remains a stub for
fundamentals delta + corporate actions; that work is queued separately.

**Live cron** (today): `Schwab-EOD` at 17:30 ET, runs `us_equities_schwab_eod.py`.

**Future EODHD orchestrator** (still queued):
1. **Schwab token health check** — read token file's
   refresh-token-expiry. If <2 days remain, log a warning (operator must
   run `us_equities_schwab_auth.py` before the weekend).
2. **Universe refresh** — Mondays only — invoke
   `us_equities_blackrock_universe.py --target russell3000` so the IWV
   reconstitution lands at the start of each week.
3. **EODHD fundamentals + corp-actions delta** — fetch only fields whose
   `last_updated` advanced since prior run; upsert to fundamentals tables.
4. **Verify** — row counts vs prior run, basic gap detection (any symbol
   missing today's bar?). Append a metrics row analogous to the political
   weekly snapshot pattern.

**Why phases are separate, not folded into one fetch**: each phase has
distinct rate-limit + failure characteristics. Token health is a 0.1s
local file read — must run first or all subsequent phases fail
opaquely. Universe refresh is BlackRock-side and only matters once a
week. Daily Schwab bars run independently via `schwab_eod.py`. EODHD
fundamentals are a different vendor with separate quota. Verify is
read-only and should always run, even if earlier phases partial-failed.

**Live ingest is NOT part of US daily**. `us_equities_schwab_live.py` runs
on its own hourly cron (1m SP500 catch-up) and stays separate from any
daily/EOD orchestrator. Same logic as the India split: live ingest and
post-close orchestration have incompatible cadences and shouldn't share
state.

---

## Lifecycle summary

| Operation | Frequency | Where to look on failure |
|---|---|---|
| US Schwab EOD daily roll | Mon–Fri 17:30 ET (21:30 UTC) | `logs/us_equities_schwab_eod_YYYYMMDD.log` |
| US Schwab live 1m catch-up | Every hour, 24/7 | `logs/us_equities_schwab_live_YYYYMMDD.log` |
| Daily India premarket | Mon–Fri 06:00 IST | `logs/india_equities_upstox_premarket_YYYYMMDD.log` |
| Live India 5-min session | Mon–Fri 09:10–15:35 IST | `logs/india_equities_upstox_live_YYYYMMDD.log` |
| Daily Political | Mon–Sat 17:30 IST | `logs/political_daily_YYYYMMDD/orchestrator.log` |
| Weekly Political | Sun 17:30 IST | `logs/political_weekly_YYYYMMDD/{orchestrator,verify}.log` |
| Hourly Political (self-healing) | every hour | `logs/political_hourly_YYYYMMDD/orchestrator.log` |
| Schwab re-auth | weekly (manual via browser) | `data/schwab/.token` mtime |
| Universe rebuild | quarterly (manual) | `configs/universes/us_*.yaml` mtime |
