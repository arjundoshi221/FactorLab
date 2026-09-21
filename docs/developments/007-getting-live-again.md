# 007 — Getting live again

> Status: `[proposed]` — drafted 2026-05-15. Sequencing doc for re-enabling the production pipelines after the sources→countries reorg. Independent of [005](005-commodities-squeeze-signals.md) (commodities is **deferred**, not part of this push).

## Decision

Re-enable the existing pipelines in a deliberate order — **notifications first**, then India, then US political, then US equities (daily + live), then an alt-data audit — and treat each step as production-only once the prior step is observably healthy.

The current state is: every pipeline has been refactored into the new `countries/{us,in_}/{equities,political}/` layout, the `factorlab.shared.{notify,runtime,ingest}` shared modules are in place, Task Scheduler entries are designed and documented in [`windows-task-scheduler.md`](../operations/windows-task-scheduler.md) — but **all scheduler tasks are disabled** and no scheduled traffic is flowing.

## Why "notifications first"

Every other pipeline calls `factorlab.shared.notify.notify(...)` or runs under `supervised(...)` which fires `notify(severity='fatal')` on any uncaught crash. If we re-enable India / political / US before notifications are proven, silent failures will go undetected. Notifications are the spine of unattended operation.

We are **not** building the daemon / PIN / Flask path right now. That's the future container-client design from [`notifier-daemon.md`](../operations/notifier-daemon.md). For today, host-side Outlook COM is enough: every scheduled script and every supervised pipeline already runs on this Windows host, so `notify(backend='outlook')` calling COM directly is sufficient. The daemon stays as code on disk for later.

## Phases

Each phase has a "done when" — observable, not aspirational. Move on only when the previous phase's done-when holds.

### Phase 1 — Outlook notification, minimal path

**Goal**: prove the existing `factorlab.shared.notify` module can put an email in the user's Outlook drafts/sent on this host, with `notify.jsonl` getting a row, in under 5 lines of caller code.

**Steps**:
1. Probe in `playground/explore/notify/` first (explore-first rule). One script: opens Outlook via `win32com.client`, sends a hardcoded test mail. No daemon, no PIN, no Flask. Confirms the COM bridge works on this user's session.
2. Once the probe sends successfully, call `factorlab.shared.notify.notify("smoke", "...", severity="warn", source="manual")` from a one-liner. Confirm:
   - Outlook draft/sent appears
   - `logs/notify.jsonl` has exactly one new line
   - `FACTORLAB_NOTIFY_TO` is read from `.env` and never echoed in stdout or the JSONL
3. Add the minimum env vars to `.env`: `FACTORLAB_NOTIFY_BACKEND=outlook`, `FACTORLAB_NOTIFY_TO=<from .env>`. Skip `FACTORLAB_NOTIFY_PIN/URL/HOST/PORT` — those are daemon-only.

**Done when**: a Python one-liner against the installed package sends a real email AND writes a JSONL row, with `FACTORLAB_NOTIFY_TO` only ever in env.

**Explicitly not in this phase**: SMTP backend, Telegram backend, the Flask daemon, Task Scheduler registration. Each is its own follow-up.

### Phase 2 — India re-enable

**Goal**: production cadence for India equities, with notifications wired into the failure path.

**Steps**:
1. Confirm Railway auth server is up (`AUTH_SERVER_URL/healthz`) and the daily Upstox token rotation still works.
2. Register and enable Task Scheduler entry `FactorLab-IndiaEquities-Upstox-PreMarket` (Daily 06:00 IST → `scripts/in/equities/upstox/india_equities_upstox_premarket.py`). Trigger once manually, watch `logs/india_equities_upstox_premarket_*.log` + `logs/notify.jsonl`.
3. Start the 5-min poller (`scripts/in/equities/upstox/india_equities_upstox_live.py --universe nifty500 --daemon`) manually — long-running daemons are not Task-Scheduler-friendly. Optionally register `FactorLab-IndiaEquities-Upstox-Live` for daily 03:40 UTC start, but understand it's a supervisor-managed process either way.
4. Force a fake-failure (e.g. unset `UPSTOX_API_KEY` temporarily, run premarket) — confirm the supervised wrapper fires `notify(severity='fatal')` and the email arrives.

**Done when**: 3 consecutive trading days of green premarket runs, 3 consecutive sessions of 5-min bars landing in `market_in.fact_equity_*`, and one verified failure-path email.

### Phase 3 — US political re-enable

**Goal**: daily / weekly / hourly Task Scheduler cadence resumes for the political pipeline.

**Steps**:
1. Inspect current `logs/political_run_state.json` and `logs/.political.lock` — clear stale lock if needed.
2. Register `FactorLab-USPolitical-Daily` / `-Weekly` / `-Hourly` (commands documented in `windows-task-scheduler.md:71-83`).
3. Trigger `--mode daily` manually first; verify exit code 0/2 (2 = non-fatal source warn is normal off-residential-IP because of Senate eFD Akamai). Inspect `logs/political_daily_<date>/orchestrator.log`.
4. Confirm `senate_efd` runs cleanly **from this Windows host** (the Akamai/residential-IP requirement still applies — must not move to cloud).
5. Watch one weekly run land + write `logs/political_metrics.jsonl`.

**Done when**: daily has run 6 consecutive days, weekly has run once with the verify step green, hourly self-heal has fired at least once for a deferred source (FEC or eFD) and recovered.

### Phase 4 — Alt-data DB audit (no new ingest)

**Goal**: take stock of what's already in the political schema before pushing new alt-data ingest.

This is an analysis phase, not a build phase. The Reddit/Twitter/arXiv source dirs were deleted in the reorg and have no current code or data. **Don't rebuild them yet** — first inventory what's in the DB.

**Steps**:
1. Audit `alt_political_us.*` row counts vs the memory snapshot (last counted 2026-05-03): legislator trades / contracts / lobbying / donations / bills / hearings.
2. For each table: row count, date range, source-coverage breakdown, deltas since the last snapshot. Write findings into `docs/architecture/ingestion-inventory.md` under a new "DB state" section.
3. Identify the **biggest gap** (memory says LDA + Congress.gov deep-fetch). Decide: backfill the gap, or move on?
4. Only after that audit, decide whether Reddit / Twitter / arXiv (true alt-social / alt-research) is worth rebuilding. They were stubs before; they don't need to come back.

**Done when**: `ingestion-inventory.md` has a current row-count table for `alt_political_us` and a one-paragraph "what's next" call.

### Phase 5 — US equities daily orchestrator (real one)

**Goal**: replace the 4-ticker EODHD demo at `scripts/us/equities/eodhd/us_equities_eodhd_daily.py` with the design target in [`orchestrators.md:347-384`](../operations/orchestrators.md) — Russell 3000, Schwab incremental, EODHD fundamentals, verify.

**Steps** (per the design target):
1. Schwab token health pre-check (warn if <2 days refresh window).
2. Mondays-only: invoke `universe.py` to refresh `configs/universes/us_russell3000.yaml` from BlackRock IWV.
3. Schwab daily-bar incremental for the R3K universe → `market_us.fact_equity_daily`.
4. EODHD fundamentals + corp-actions delta (only fields whose `last_updated` advanced).
5. Verify: row counts vs prior, missing-bar detection, append metrics row.

**Trigger**: Task Scheduler Mon–Fri 21:30 UTC (= 16:30 ET, post-close). Notifications wired into each phase's failure path.

**Done when**: 5 consecutive post-close runs green, `market_us.fact_equity_daily` covers all R3K names for those dates, and one EODHD fundamentals delta has landed.

### Phase 6 — US equities live (Schwab session daemon)

**Goal**: turn on `scripts/us/equities/schwab/us_equities_schwab_live.py` for the full US session (08:30–16:00 ET), with sp500 5m + watchlist 1m.

This phase remains manual-start (or Task Scheduler trigger at 08:30 ET) because it's a session-long daemon, same operational shape as the India 5-min poller.

**Done when**: one full US session of 5-min bars in `market_us.fact_equity_5m` for sp500, one of 1-min for the watchlist, and a fresh `data/_heartbeat/factlab_us_live` mtime through the session.

## Things explicitly out of scope

- **Commodities** (`005-commodities-squeeze-signals.md`) — deferred. Re-evaluate once Phases 1–6 are green.
- **The notifier Flask daemon + PIN + container-client path** — designed but unused until we actually run a non-host process that needs to send alerts. Outlook COM from the host covers every current caller.
- **SMTP / Telegram backends** — the code is there, the env vars are documented, but Outlook is enough for v1. Add as redundancy only after one of: (a) Outlook fails repeatedly, (b) we need mobile alerts off-host.
- **Reddit / Twitter / arXiv ingest rebuild** — gated on the Phase 4 audit. Likely deferred.
- **IBKR Lite → Pro upgrade + Snapshot Bundle** — required for the "tier 5" validation feed in [002](002-live-us-market-data.md), but not blocking any of Phases 1–6.

## Open questions

- Phase 1: do we want `notify.jsonl` rotated, or is append-forever fine? (Probably fine until it crosses 100 MB.)
- Phase 4: where do the audit numbers live — committed in `ingestion-inventory.md`, or written to `logs/political_metrics.jsonl` and referenced? Decision before starting the audit.
- Phase 5: EODHD fundamentals coverage on the free tier is partial. Do we upgrade to the paid plan before running the daily, or accept partial coverage initially?

## Pointer

When this plan lands, the per-phase outcomes migrate to:
- Notification operational details → [`docs/operations/notifier-daemon.md`](../operations/notifier-daemon.md) (existing) + a new "host-only Outlook quickstart" section
- India / political / US daily / US live operational details → [`docs/operations/orchestrators.md`](../operations/orchestrators.md) (existing — update "Current state" rows)
- DB-state audit output → [`docs/architecture/ingestion-inventory.md`](../architecture/ingestion-inventory.md) (new "DB state" section)
