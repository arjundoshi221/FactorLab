# Political Pipeline — Operations Runbook

> Status: live as of 2026-06-19. Last updated: 2026-06-19.

This is the operator's reference for the US-Congress political-data pipeline:
how to run it, where to look when something fires, and how to add new sources
or curate overrides. For the schema design rationale see
[`docs/architecture/database.md`](../../architecture/database.md).
For the data-source catalog see
[`senator-trades.md`](senator-trades.md).

## Querying trades — table vs view

There are two queryable surfaces for legislator trades. Pick the right one:

| Surface | Use for | Why |
|---|---|---|
| `alt_political_us.legislator_trades_dedup` (view) | **All analytics, factor construction, signal queries** | Source-deduped. One row per logical trade. Adds `source_code` column. Implements precedence: eFD > paper-LLM > SSW historical. Same-source PTR amendments collapsed. NULL-key rows pass through. Migration [028](../../migrations/versions/028_legislator_trades_dedup_view.py). |
| `alt_political_us.legislator_trades` (raw table) | Audit trail, provenance, ingest idempotency, schema work | Append-only multi-source. Will contain logical duplicates across sources for any year covered by both `senate_stock_watcher_historical` (2012-2020) and `senate_efd_ptr` (2019-today). |

The view is non-materialized — no `REFRESH` step. Reads are cheap given table size (~70K rows). New ingests show up in the view immediately.

---

## Quick reference — manual triggers

### Bring everything up-to-date right now (one-shot)

The standard daily orchestrator. Pulls fresh data for all active-period
sources (House Clerk current year, Congress 119 bills/hearings, LDA 2026,
FEC current cycle, legislators YAML). Senate eFD runs *headless* — if
Akamai blocks (see "Known operational issues"), use the headed eFD command
below.

**PowerShell (Windows host):**

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
& "C:\Users\arjd2\.conda\envs\factorlab\python.exe" scripts/us/political/us_political_daily.py --mode daily *>&1 | Tee-Object -FilePath "logs/factlab_political_daily_$ts.log"
```

**Bash / git-bash:**

```bash
PY="C:/Users/arjd2/.conda/envs/factorlab/python.exe"
ts=$(date +%Y%m%d_%H%M%S)
$PY scripts/us/political/us_political_daily.py --mode daily 2>&1 | tee "logs/factlab_political_daily_${ts}.log"
```

Runtime: ~5-15 min depending on LDA volume. Output also lands in
`logs/political_daily_<YYYYMMDD>/orchestrator.log`.

### Senate eFD catch-up (headed — required while headless is blocked)

When the daily run reports `[senate_efd] FAIL` from an Akamai timeout, do
this from your local Windows machine (Playwright opens a visible browser):

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
& "C:\Users\arjd2\.conda\envs\factorlab\python.exe" scripts/us/political/us_political_backfill.py --phase 2 --house-clerk-limit 0 --senate-efd-headed --senate-efd-from 2026-06-10 --senate-efd-to (Get-Date -Format "yyyy-MM-dd") *>&1 | Tee-Object -FilePath "logs/factlab_senate_efd_catchup_$ts.log"
```

Adjust `--senate-efd-from` to cover the window you missed (eFD search is
fast, can safely cover several weeks). `--house-clerk-limit 0` skips the
House Clerk phase so it doesn't duplicate work the daily already did.

### Other modes

```powershell
$PY = "C:\Users\arjd2\.conda\envs\factorlab\python.exe"

# Weekly cycle (heavier — adds USAspending + Finnhub + anomaly snapshot + verify)
& $PY scripts/us/political/us_political_daily.py --mode weekly

# Verify-only — audit + metrics snapshot, no ingestion
& $PY scripts/us/political/us_political_daily.py --mode verify-only

# Standalone audit (called by weekly mode internally)
& $PY scripts/us/political/us_political_verify.py

# Status snapshot — current DB counts per source (read-only)
& $PY scripts/us/political/us_political_status.py
```

Output lands in `logs/political_<mode>_YYYYMMDD/` with `orchestrator.log` and
(weekly only) `verify.log`.

## Current data state (2026-06-19)

Snapshot of `alt_political_us`. Re-run `us_political_status.py` for live numbers.

### Legislator trades (`legislator_trades`)

| Source (`endpoint_id`) | Filing range | Txn range | Rows |
|---|---|---|---|
| House Clerk PTR (7) | 2014-01-02 → **2026-06-17** | 2012-02-27 → 3031-04-30¹ | 53,728 |
| Senate eFD PTR (9) | 2016-01-07 → 2026-06-16 | 2014-12-03 → 2026-05-27 | 6,567 |
| Senate Stock Watcher (11) — frozen historical | 2012-07-14 → 2021-01-01 | 2012-06-14 → 2020-12-02 | 15,821 |
| Paper-LLM (30) | 2021-02-10 → 2022-02-07 | 2021-01-22 → 2022-01-12 | 64 |

¹ Three rows have garbage transaction-date years (2202/2220/3031) from
source-PDF typos. Cosmetic only — `legislator_trades_dedup` view filters
them. See [`historical-loads.md`](historical-loads.md).

### Congress.gov

| | 117 | 118 | 119 |
|---|---:|---:|---:|
| Bills (total) | 17,828 | 19,315 | 15,433 |
| Priority-area bills² | 10,454 | 11,284 | 9,027 |
| Hearings | 2,211 | 2,139 | 753 |

² Priority policy areas — the 15-area set Pass C deep-fetches. Defined in
[`ingest.py`](../../../src/factorlab/countries/us/political/congress_gov/ingest.py)
as `PRIORITY_POLICY_AREAS`. Bills outside the set are listed (Pass A) and
have policy_area populated (Pass B), but cosponsors/committees/actions
are intentionally not fetched.

Deep-fetch coverage **(of priority-area bills only — the right denominator)**:
- `bill_actions`: 100% (30,765 / 30,765) — 104,829 action rows
- `bill_committees`: 98.0% (30,213 / 30,765) — 44,237 rows. The 2% gap is
  bills with no committee referral at fetch time, expected.
- `bill_sponsors`: ~complete (52,565 distinct bills across all areas;
  primary sponsor is fetched in Pass B for every bill)

### LDA lobbying

1,053,881 filings · 2,140,673+ activities — 2014-2026 fully loaded.
Filings/year ranges 72k (2016) → 108k (2025); 2026 YTD = 28,819.
**The "LDA basically empty" gap from May 2026 is closed.**

### FEC, USASpending, Finnhub

Per memory: FEC Mode A complete (~1.68M rows), Mode B 49% done.
Contracts: 382,996 rows (220k USASpending direct + 155k Finnhub mirror).
No change vs prior audit — see `us_political_status.py` for fresh counts.

## Known operational issues (open as of 2026-06-19)

| Issue | Symptom | Status |
|---|---|---|
| **Senate eFD headless mode blocked** | Headless Chromium hits Akamai challenges (`Page.goto` timeout on first goto, or `Locator.check` timeout on the agreement checkbox if you get past the redirect). Headed mode (`--senate-efd-headed`) works reliably. Minimal stealth tweaks in [`senate_efd/scraper.py`](../../../src/factorlab/countries/us/political/senate_efd/scraper.py) (hide `navigator.webdriver`, disable Blink automation feature) help with the checkbox-rendering case but don't fully clear Akamai. | Daily orchestrator marks eFD non-fatal; manual backfill must use `--senate-efd-headed`. Full headless fix needs `playwright-stealth` package or a real residential-proxy headless solution. |
| **Hearings 119 partial** | Only 663 vs 2,211 (117) / 2,139 (118). Expected to grow naturally — 119 is still active. | Not a bug; passive growth via daily list+detail. |

### Cache-freshness mechanism (added 2026-06-19)

The HTTP client at [`shared/ingest/http.py`](../../../src/factorlab/shared/ingest/http.py)
historically cached every `save_as=` response to disk **forever**. That was
fine for immutable archives (closed congresses, prior LDA years, closed FEC
cycles) but silently broke live-updating sources (House Clerk current-year
index, Congress.gov 119, LDA current year, FEC current cycle): once a page
landed on disk, subsequent runs would short-circuit on `disk_path.exists()`
and never see new upstream rows. This is what caused the 2026-04-28 House
Clerk wedge (caught + fixed 2026-06-19).

The mechanism is now uniform: `HTTPClient.get/post/_fetch` accept a
`max_age_sec: float | None` kwarg. `None` (default) = legacy forever-cache.
A positive value = treat cache hit as miss if `mtime` is older. `0` = always
refetch. The freshness policy lives per-source:

| Source | Active scope | TTL |
|---|---|---|
| House Clerk year-index ZIP | current calendar year | `max_age_sec=0` (always refetch) |
| Congress.gov bills/hearings | `ACTIVE_CONGRESSES = {119}` | 3,600 s |
| LDA filings list | current calendar year | 3,600 s |
| FEC Schedule A | `active_cycle()` (computed from year) | 3,600 s |

Closed periods (117/118, prior LDA years, prior FEC cycles) continue to
cache forever — re-running a backfill costs zero API quota for immutable
data.

**When a new congress / cycle begins**: set `POLITICAL_ACTIVE_CONGRESSES` in
`.env` (comma-separated, e.g. `119,120` during the Jan transition window).
FEC's `active_cycle()` is computed dynamically — no env edit needed.

**Env vars (all optional, sensible code defaults):**

```dotenv
# Active congress set for Congress.gov cache TTL. Comma-separated ints.
# Default: 119. Update when Congress 120 begins (Jan 2027).
POLITICAL_ACTIVE_CONGRESSES=119

# Cache TTL for live-updating sources (Congress.gov 119, LDA current year,
# FEC active cycle). Seconds. Lower = fresher but more API quota burn.
# Default: 3600 (1 hour).
POLITICAL_CACHE_TTL_ACTIVE_SEC=3600
```

### Recently closed (2026-06-19)

- ✅ **House Clerk current-year index wedge** — see above. Max filing date
  2026-04-28 → 2026-06-17 after the cache-freshness work landed. Initial
  patch was a manual `unlink()` in [`index.py`](../../../src/factorlab/countries/us/political/house_clerk/index.py);
  generalised into the shared `max_age_sec=0` plumbing.
- ✅ **Congress.gov / LDA / FEC active-period staleness** — same class of
  bug as House Clerk; the previous "forever cache" would silently miss
  upstream updates. Fixed via the same `max_age_sec` mechanism. First post-fix
  daily run picked up +90 119-hearings and +2,053 LDA 2026 filings that had
  accumulated upstream while the local cache was holding old pages.
- ✅ **Congress.gov Pass C "coverage gap" myth** — earlier framing in this
  doc reported "~58% deep-fetch coverage" by counting Pass C against all
  bills. The correct denominator is *priority-area bills* (the 15-area set
  the code deliberately filters to); against that, actions is 100% and
  committees is 98%. No code change needed; Pass C is operating as designed.

## Mode contract

| Mode | What runs | Cadence | Typical runtime |
|---|---|---|---|
| `daily` | legislators YAML refresh, House Clerk current year, Senate eFD last 7 days, LDA current year, Congress.gov list+detail, hearings | Mon–Sat 17:30 IST | ~5–15 min |
| `weekly` | Daily steps + USAspending current FY + Finnhub contracts + full-year LDA + FEC current cycle + Congress.gov deep + anomaly snapshot + `verify_political` audit | Sun 17:30 IST | ~30–60 min |
| `hourly` | **Self-healing pass.** Re-runs only sources that are out of cooldown AND haven't succeeded in the last `--hourly-cooldown` hours (default 6h). Deferred 429s (FEC quota exhausted, Akamai timeouts, vendor outages) get picked up automatically without manual intervention. | Every hour 24/7 | <5 s when nothing's eligible; otherwise a few-minute partial daily |
| `verify-only` | `verify_political` + metrics snapshot. No ingest. | On demand | <30 s |

### How the hourly retry works

Every source's outcome (ok / fail / deferred) is recorded in
`logs/political_run_state.json`. Two cooldown rules gate retries:

1. **Deferred-cooldown** — if `_client.py` saw a `429 Retry-After` longer than
   the inline cap (default 600s), it raised `RateLimitDeferred` instead of
   blocking the run. The orchestrator records the deferral with
   `retry_after_at = now + Retry-After`. No retry until that timestamp.
2. **Success-cooldown** — if a source already succeeded inside the last
   `--hourly-cooldown` hours, skip it. Avoids hammering APIs that just
   gave us fresh data.

When neither rule blocks, `--mode hourly` runs the source. State updates
on every outcome.

Concrete walk-through of the FEC 429 case:

```
Mon 06:00 UTC  — daily run; FEC ingest hits 429, Retry-After=1623s.
                 Client raises RateLimitDeferred. State updates:
                   fec.retry_after_at = "Mon 06:27 UTC"
                 Daily run continues, exits with warn (FEC is in NON_FATAL_SOURCES).
Mon 07:15 UTC  — hourly task fires. should_run('fec') -> SKIP "deferred (-720s)".
                 Wait, retry_after_at < now, so actually eligible. FEC runs.
                 FEC quota has reset → success → state updates:
                   fec.last_ok = "Mon 07:15 UTC", retry_after_at = None
Mon 08:15 UTC  — hourly task fires. should_run('fec') -> SKIP "recently succeeded (1.0h ago)".
... and so on through the day.
Sun 17:30 UTC  — weekly run; FEC succeeds normally (or defers again if cycle is heavy).
```

CLI flags:

- `--dry-run` — parse + classify, don't write to DB
- `--skip-senate-efd` — skip Playwright (use on non-residential / cloud hosts)
- `--skip-contracts` — skip USAspending + Finnhub (heavy; weekly only)
- `--skip-verify` — skip the verify_political audit (weekly only)
- `--lock-file <path>` — file-based mutex (default `logs/.political.lock`)

## Exit codes

| Code | Meaning | Task Scheduler shows |
|---:|---|---|
| 0 | OK | success |
| 2 | Warn (non-fatal source fail or warn-level anomaly) | success-with-warning |
| 3 | Fatal (DB / schema / fail-level anomaly) | fail |
| 75 | Lock held (concurrent run already in progress) | "task did not run" |

Non-fatal sources (failure → warn, not fatal): `senate_efd`, `lda`,
`usaspending`, `finnhub_contracts`. House Clerk is fatal — it's the spine of
new-PTR detection and a silent gap there is unacceptable.

## Task Scheduler setup

Run **once** as Administrator:

```cmd
scripts\setup_political_scheduler.bat
```

Creates two tasks:

- `FactorLab-USPolitical-Daily` — Mon-Sat 17:30 IST (= 12:00 UTC)
- `FactorLab-USPolitical-Weekly` — Sun 17:30 IST

Manage:

```powershell
Get-ScheduledTask -TaskName "FactorLab-Political*" | Format-Table TaskName, State, NextRunTime
Disable-ScheduledTask -TaskName FactorLab-USPolitical-Daily   # temp pause (e.g. before a migration)
Enable-ScheduledTask  -TaskName FactorLab-USPolitical-Daily
schtasks /run /tn FactorLab-USPolitical-Daily                 # ad-hoc run
```

## Anomaly detection (weekly only)

Each weekly run captures 8 metrics and appends to
`logs/political_metrics.jsonl`. The current snapshot is diffed against the
prior one; threshold breaches append to `logs/political_alerts.jsonl` and
fire a Win10 toast (best-effort — falls back to log+JSONL only).

| Metric | Source | Threshold |
|---|---|---|
| `total_trades` | `count(*)` on `legislator_trades` | drop > 0 absolute → fail |
| `trades_last_7d` | `WHERE ingested_at >= now() - 7d` | (informational) |
| `bioguide_resolve_rate` | `count(bioguide_id) / count(*)` | < 0.99 warn, < 0.97 fail |
| `ticker_resolve_rate` | `count(ticker) / count(*)` | drop > 2pp WoW → warn |
| `tier2_unresolved_new` | NULL bioguide rows ingested in last 7d | > 25 → warn |
| `lda_filings_current_year` | `lobbying_filings WHERE filing_year=THIS` | drop > 0 → fail |
| `gov_contracts_current_fy` | `gov_contracts WHERE action_date >= jan 1` | drop > 0 → fail |
| `raw_archive_growth_7d` | `audit.raw_archive WHERE fetched_at >= now() - 7d` | < 5 → warn (ingest broken?) |

Alerts are append-only — never overwritten. Triage path:

1. `tail -n 20 logs/political_alerts.jsonl` to see recent fires
2. For each, check the matching `orchestrator.log` and `verify.log` in the
   per-run dated dir
3. Cross-check against `logs/political_metrics.jsonl` to see the trend

## Senate eFD historical backfill (one year at a time)

Senate eFD requires Playwright + a residential IP — Akamai blocks cloud /
VPS / datacenter IPs. Backfill is run from your local Windows machine,
one filing-year at a time, in a headed Playwright window so you can
intervene if Akamai serves a CAPTCHA.

```powershell
# One year, headed, max 500 filings (sufficient for any year — typical year is 100-1500 PTRs)
python scripts/us/political/backfill.py --phase 2 `
    --senate-efd-from 2018-01-01 --senate-efd-to 2018-12-31 `
    --senate-efd-headed --senate-efd-max-filings 500 2>&1 | Tee-Object logs/senate_efd_2018.log
```

### Critical gotcha — single-batch upsert

The eFD ingest pipeline **does not commit per-filing**. It scrapes the
search results, downloads every HTML/GIF, parses each one to build a
`pending` list in memory, and `fast_upsert`s the entire list once at the
end. If the process dies after `[senate_efd] downloaded N filings` but
before `[senate_efd] done: IngestResult(...)`, **zero rows reach the DB**.

Verification a run actually committed:

```bash
PY="C:/Users/arjd2/.conda/envs/factorlab/python.exe"
$PY -c "
from factorlab.storage.db import get_engine
from sqlalchemy import text
with get_engine().begin() as c:
    print(c.execute(text(\"\"\"
      SELECT COUNT(*) FROM alt_political_us.legislator_trades t
      JOIN ref.data_endpoints e ON e.id=t.endpoint_id
      WHERE e.code='senate_efd_ptr'
        AND filing_date BETWEEN 'YYYY-01-01' AND 'YYYY-12-31'
    \"\"\")).scalar())
"
```

If the count is 0, re-run the same command. The downloaded HTML/GIF
files persist on disk under `data/political/raw/senate_efd/`, so the
second pass skips re-downloading and goes straight to parse + upsert.

### Backfill progress tracker (2026-05-03)

| Filing year | Status |
|---|---|
| 2014-2018 | Not run via eFD direct (SSW historical covers — see §2A in data-source doc) |
| 2019 | ✅ 328 rows |
| 2020 | ✅ 314 rows |
| 2021 | ✅ 678 rows |
| 2022 | ✅ 738 rows |
| 2023 | ✅ 1,156 rows |
| 2024 | ✅ 937 rows |
| 2025 | ✅ 666 rows (rolling — keep current via daily incremental) |
| 2026 | ✅ 345 rows (rolling — current year, daily) |

When running 2014-2018, expect logical duplicates against
`senate_stock_watcher_historical`. The
`legislator_trades_dedup` view collapses them automatically (eFD wins
precedence). Decision deferred — running the older years adds direct-source
provenance and paper-PTR placeholder records but no new analytical signal.

## Adding a new source

Follow the existing pattern in `src/factorlab/sources/political/<name>/`:

1. **Add a vendor row** if the source organization isn't already in
   `ref.vendors`. Add an endpoint row to `ref.data_endpoints` (FK to vendor).
   Both via a new alembic migration. The endpoint code is what the source
   module passes to `lookup_endpoint_id`.

2. **Write `<name>/ingest.py`**:
   - Resolve the endpoint id once at function start:
     `ENDPOINT_ID = lookup_endpoint_id(engine, "<endpoint_code>")`
   - Build row dicts with `"endpoint_id": ENDPOINT_ID` (NOT `"source": "..."`)
   - Use `_trades.flush_legislator_trades(engine, rows)` for trade tables
   - Strict-NULL: write `None` for unknowns, never fabricated defaults

3. **Wire into the orchestrator**: add a `_run_source(...)` call inside the
   appropriate mode (daily / weekly) in `us_political_daily.py`. Decide
   if failure should be fatal (House Clerk) or non-fatal (everything else)
   and add to `NON_FATAL_SOURCES` set if so.

4. **Add tests** in `tests/political/`. At minimum: a parser unit test and
   a smoke run against a real fixture.

## Adding a manual bioguide override

Some legislators file under names the strict matcher cannot bridge (e.g.
Van Taylor files as "Nicholas V. Taylor"). To add one:

```sql
INSERT INTO alt_political_us.legislator_aliases
    (country_code, alias_normalized, bioguide_id, source, confidence, verified_via)
VALUES (
    'US',
    lower('Some Filed Name'),    -- match exactly how PTR/eFD spells it
    'X000123',                   -- the bioguide id you verified
    'manual_pdf_review',
    'verified',
    'evidence pointer (PDF path / URL / doc_id)'
);
```

The matcher consults this table as **Stage 0** (highest priority — beats
all fuzzy stages). No code change needed; takes effect on next ingest run.

Verify the override resolves:

```python
from factorlab.sources.political._bioguide import StrictBioguideMatcher
from factorlab.storage.db import get_engine
m = StrictBioguideMatcher(get_engine())
print(m.resolve(full_name="Some Filed Name", chamber="rep", trade_date=None))
```

## Triage table — what to look at when something fires

| Symptom | Where to look | Likely cause |
|---|---|---|
| Daily exit 2 (warn) | `logs/political_daily_*/orchestrator.log` | senate_efd Akamai block, LDA timeout, etc. — non-fatal |
| Daily exit 3 (fatal) | same | House Clerk schema mismatch, DB conn loss, OOM in PDF parser |
| Daily exit 75 (lock held) | check `logs/.political.lock` content | prior run still running OR crashed without releasing (12h stale → next run steals) |
| Weekly anomaly: total_trades dropped | `logs/political_alerts.jsonl` | data loss — investigate IMMEDIATELY |
| Weekly anomaly: bioguide_rate < 0.99 | `verify_political` output | new filer the matcher can't bridge — add to `legislator_aliases` |
| Weekly anomaly: tier2_unresolved_new > 25 | recent ingest logs | parser regression OR genuinely new filers |
| Weekly anomaly: raw_archive_growth_7d < 5 | network logs / vendor status | upstream API down OR a vendor key expired |

## Re-enabling India tasks after a maintenance window

```powershell
Enable-ScheduledTask -TaskName FactorLab-IndiaEquities-Upstox-Live
Enable-ScheduledTask -TaskName FactorLab-IndiaEquities-Upstox-PreMarket
```

The political tasks (`FactorLab-USPolitical-*`) are independent and unaffected
by the India schedule.
