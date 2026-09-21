# 008 — Script naming convention + India historical build-out

> Status: `[accepted]` — drafted 2026-05-16; reviewed by turing / ritchie / heimdall / factorlab-pm 2026-05-16; revised in same session. India-first; US rename is a follow-up. Builds on [007](007-getting-live-again.md). Sequencing: 008 lands BEFORE 007 Phase 2 (Task Scheduler re-enable) — otherwise Phase 2 would register tasks against the soon-renamed paths.
>
> **2026-05-16 correction**: an earlier draft of this doc proposed *flat* layout under `scripts/`. After two rounds of user feedback, production layout is `scripts/{country}/{domain}/{vendor}/<file>.py` mirroring the Python source layer (`factorlab.countries.in_.equities.upstox.*` ↔ `scripts/in/equities/upstox/*`). Filename also carries the full `{country}_{domain}_{vendor}_{action}` tag for self-describing Task Scheduler entries / grep output. Filename + folder tree are intentionally redundant — folder for browsing, filename for grep + scheduler entries.

## Decision

Adopt a single script-naming convention — **`{country}_{domain}_{vendor}_{action}.py`** — across all production entrypoints in `scripts/`, with a **mirrored country/domain/vendor folder structure** so the filename's tag and the directory tree convey the same information at different granularities. India scripts get renamed and a new historical loader is added; US follows after India is green.

## Why now

- Task Scheduler entries show the script *path* as the action. Today's path (`scripts\in_\equities\live.py`) hides what the task does — you have to read the wrapper docstring to know it's the Upstox 5-min intraday poller. The filename should answer "what country, what asset, what vendor, what cadence" by itself.
- The new file `india_equities_upstox_historical.py` doesn't exist yet — adding it is the right moment to fix the inconsistency with `premarket.py` / `live.py`.
- Historical Upstox backfill is a real gap (no `scripts/in_/equities/backfill.py` today). Live data is moving; historical isn't.

## Convention

```
{country}_{domain}_{vendor}_{action}.py
```

Vocab (fixed; extend deliberately):

| Slot     | Allowed values                                                |
|----------|---------------------------------------------------------------|
| country  | `india`, `us`, `eu`, `apac`, `cross` (multi-country tooling)  |
| domain   | `equities`, `political`, `fundamentals`, `derivatives`, `fx`  |
| vendor   | `upstox`, `schwab`, `eodhd`, `ibkr`, `house_clerk`, `fec`, ...|
| action   | `live`, `historical`, `premarket`, `auth_server`              |

Action-only services (no vendor coupling) keep the same shape — e.g. shared utilities live in `scripts/_shared/`, not in the production task root.

## Directory layout (post-rename)

```
scripts/
├── in/                                              [country]
│   └── equities/                                    [domain]
│       └── upstox/                                  [vendor]
│           ├── india_equities_upstox_premarket.py
│           ├── india_equities_upstox_live.py
│           ├── india_equities_upstox_auth_server.py
│           └── india_equities_upstox_historical.py  [NEW]
├── us/                                              [country folder — US rename pending; legacy substructure]
│   ├── equities/
│   └── political/
└── _shared/                                         [utilities only — unchanged]
```

Mirrors the Python source layer: `factorlab.countries.in_.equities.upstox.*` ↔ `scripts/in/equities/upstox/*`. Source uses `in_` (Python keyword), scripts use `in` (filesystem only). Filename + folder tree are intentionally redundant: folder for browsing, filename for grep / scheduler / log lines.

## India rename map

| Current path                                  | New path                                              |
|-----------------------------------------------|-------------------------------------------------------|
| `scripts/in_/equities/premarket.py`           | `scripts/in/equities/upstox/india_equities_upstox_premarket.py`       |
| `scripts/in_/equities/live.py`                | `scripts/in/equities/upstox/india_equities_upstox_live.py`            |
| `scripts/in_/equities/auth_server.py`         | `scripts/in/equities/upstox/india_equities_upstox_auth_server.py`     |
| *(none)*                                      | `scripts/in/equities/upstox/india_equities_upstox_historical.py` *(new)* |

`git mv` preserves blame.

## Touched-by-rename surface

| What                                           | Change                                                       |
|------------------------------------------------|--------------------------------------------------------------|
| `sys.path.insert(...)` parent depth            | `parents[3]` → `parents[1]` (now in flat `scripts/`)         |
| `Procfile`                                     | `--chdir scripts/in_/equities` → `--chdir scripts/in/equities/upstox` |
| `railway.json`                                 | same chdir change                                            |
| `docs/operations/windows-task-scheduler.md`    | all India entries get new paths + renamed task IDs           |
| `docs/operations/orchestrators.md`             | "Script" column updated; task-ID column updated              |
| `docs/architecture/ingestion-inventory.md`     | Scripts table updated                                        |
| `docs/operations/notifier-daemon.md`           | no change (already at `scripts/_shared/`)                    |
| Memory (`MEMORY.md` + `project_*.md`)          | grep for old paths and update                                |

## Task Scheduler entry IDs

| Old ID                          | New ID                                       |
|---------------------------------|----------------------------------------------|
| `FactorLab-India-PreMarket`     | `FactorLab-IndiaEquities-Upstox-PreMarket`   |
| `FactorLab-India-5min`          | `FactorLab-IndiaEquities-Upstox-Live`        |
| *(none)*                        | *(historical is manual one-shot — no task)*  |

## Updated usage docstring shape

Every renamed/new script gets the same canonical top-of-file block:

```python
"""india_equities_upstox_live — Live 5-min intraday candle poller for Indian equities.

Pattern:    country_domain_vendor_action
Country:    India (NSE; BSE pending)
Domain:     equities (cash + nearest-expiry futures)
Vendor:     Upstox V3 (OAuth code-grant, daily token rotation)
Action:     live (long-running session daemon, NOT Task-Scheduler-friendly past start)

Run model:  Long-running process (internal sleep loop)
Schedule:   Task Scheduler `FactorLab-IndiaEquities-Upstox-Live`, daily 03:40 UTC start
Duration:   ~6h alive (03:40-10:05 UTC = 09:10-15:35 IST)
Cadence:    equities every 5 min · nearest-expiry futures every 10 min (staggered)
Rate use:   ~1,800/2,000 Upstox calls per 30 min at fo_eligible (~200 symbols)
Output:     Postgres market_in.fact_equity_intraday (canonical) + Arrow IPC cache
Health:     Hourly "session nominal" email; --health-interval seconds (0 disables)
Failure:    factorlab.shared.notify.notify(severity='fatal') on uncaught crash

Usage:
    python scripts/in/equities/upstox/india_equities_upstox_live.py --universe nifty500 --daemon
    python scripts/in/equities/upstox/india_equities_upstox_live.py --universe fo_eligible --daemon --health-interval 1800

Docs:
    docs/data-sources/india/upstox.md          — vendor integration
    docs/operations/orchestrators.md            — schedule + failure modes
    docs/operations/windows-task-scheduler.md  — Task Scheduler registration
"""
```

## India historical script

**v1: CLI-only.** No YAML config. (Reviewer ruling — Turing flagged premature abstraction; 7/9 candidate config fields are CLI-trivial. Add `--config` support only after the same flag-set is invoked twice in real use.)

### CLI

```bash
# Default invocation — pulls daily bars for nifty500 equities back ~20 years
python scripts/in/equities/upstox/india_equities_upstox_historical.py --universe nifty500

# Multi-product, multi-interval
python scripts/in/equities/upstox/india_equities_upstox_historical.py \
    --universe nifty500 \
    --products equities,futures \
    --intervals 1d,30m \
    --from-date 2010-01-01 \
    --to-date 2026-05-15 \
    --workers 4

# Dry-run plans the work but makes zero API/DB calls
python scripts/in/equities/upstox/india_equities_upstox_historical.py --universe nifty50 --dry-run
```

### CLI flag inventory (v1)

| Flag                       | Default                | Notes                                                          |
|----------------------------|------------------------|----------------------------------------------------------------|
| `--universe`               | required               | Name from `configs/universes/india.yaml`                       |
| `--products`               | `equities`             | Comma list: `equities,futures` (nearest expiry only in v1)     |
| `--intervals`              | `1d`                   | Comma list: `1d,30m,15m,1m` — clamped to Upstox lookback       |
| `--from-date`              | per-interval clamp     | YYYY-MM-DD; daily back ~20yr, 30m ~5yr, 1m ~recent             |
| `--to-date`                | today (UTC)            | YYYY-MM-DD                                                      |
| `--write-postgres`         | true                   | `--no-write-postgres` to disable                                |
| `--write-parquet`          | true                   | `--no-write-parquet` to disable                                 |
| `--workers`                | `4`                    | Pooled symbol fetches                                          |
| `--rate-budget-per-30min`  | `1800`                 | Leaves 200 buffer under Upstox 2000/30min cap                  |
| `--dry-run`                | `false`                | Plan only; no API or DB calls                                  |

### Behavior

1. Load config; CLI flags override file values.
2. Auth: `ensure_token(interactive=False)` — same Railway-fetch path as live/premarket.
3. Universe + instruments: reuse `load_universe` / `find_equities` / `find_nearest_future`.
4. For each (symbol × interval × product), call Upstox V3 `/historical-candle/{key}/{interval}/{to}/{from}` in pooled workers under a rate-budget guard.
5. Write via `factorlab.storage.ingest.write_candles` (Postgres canonical) + Parquet (analytical) — idempotent on `(instrument_id, bar_time)`.
6. Reuse shared/runtime: `setup_logging`, `supervised(...)`, `HealthReporter` configured with `frequency="historical one-shot"` and `force_report(reason="backfill complete")` at end.
7. Crash notify carries `vendor=Upstox, country=IN, domain=equities`.

### Out-of-scope for v1
- Options (futures only — most options are cash-settled weekly, less factor signal)
- Tick data (1m is the floor; <1m needs WebSocket, not REST)
- Multi-config orchestration (one config = one run; chain manually)

## Order of operations (revised post-review)

| #  | Step                                                                                          | Owner        | Done-when (sharpened)                                                                                                       |
|----|-----------------------------------------------------------------------------------------------|--------------|------------------------------------------------------------------------------------------------------------------------------|
| 1  | Land this doc (008) — review verdicts appended at bottom                                      | factorlab-pm | 4 verdicts (turing / ritchie / heimdall / factorlab-pm) present in "Reviews" section below                                  |
| 2  | Patch 3 India scripts: `parents[3]→parents[1]` + `PROJECT_ROOT` assertion + canonical docstring | ritchie      | `python scripts/in/equities/upstox/india_equities_upstox_premarket.py --help` exits 0 from repo root for all 3; assertion present in each      |
| 3  | `git mv` the 3 India scripts; remove now-empty `scripts/in_/`                                  | ritchie      | `git log --follow` shows continuity; `scripts/in_/` directory does not exist                                                  |
| 4  | Update Procfile + railway.json: `--chdir scripts` **AND** `auth_server:app` → `india_equities_upstox_auth_server:app` | ritchie | Procfile and railway.json reference both the new chdir AND the new module path                                              |
| 4b | Verify exactly one Flask app under flat `scripts/`                                             | ritchie      | `grep -rn "app = Flask" scripts/` returns exactly 1 hit (auth_server)                                                       |
| 5  | Update docs: `orchestrators.md` (kill legacy `factlab_*` refs, point at new paths), `windows-task-scheduler.md`, `ingestion-inventory.md`, `data-sources/india/upstox.md` | factorlab-pm | `grep -rn "scripts/in_" docs/` returns 0 hits; `grep -rn "factlab_india_" docs/` returns 0 hits                              |
| 6  | Update `MEMORY.md` + `project_*.md` entries referencing old paths                              | factorlab-pm | `grep -rn "scripts/in_\|factlab_india_" C:/Users/arjd2/.claude/projects/...factorlab/memory/` returns 0 hits                  |
| 7  | Build `india_equities_upstox_historical.py` (CLI-only, no YAML)                                | coder        | (a) `--dry-run` prints plan + 0 API calls; (b) one symbol round-trips to Postgres; (c) re-run lands 0 new rows (idempotent)  |
| 8  | Task Scheduler: `schtasks /delete /tn` old IDs (`FactorLab-India-PreMarket`, `FactorLab-India-5min`); then `/create` new IDs (`FactorLab-IndiaEquities-Upstox-{PreMarket,Live}`) | ritchie | `schtasks /query` shows new IDs, no old IDs remain; `schtasks /run /tn "FactorLab-IndiaEquities-Upstox-PreMarket"` fires green |
| 9  | Smoke-test live (5 min) + premarket + historical (one symbol) from new locations              | factorlab-pm | premarket: instruments cached + 1 notify; live: 1 sweep + 1 health-info notify (with --health-interval 60); historical: ≥1 row in `market_in.fact_equity_daily` for AAPL-equivalent ticker; all three crash-paths verified via forced failure |

## Out of scope (deferred)

- **US rename** — same pattern, after India is green. Adds: `us_equities_schwab_{live,historical}.py`, `us_equities_eodhd_daily.py`, `us_political_{daily,backfill}.py`.
- **Political rename** — political has 8 vendors; the convention naturally suggests `us_political_house_clerk_historical.py` etc. for the per-vendor maintenance scripts. Bigger surface — separate doc.
- **Notification rollout to existing backfill scripts** — orthogonal; will happen during the rename pass since we're already touching the files.

## Reviews (2026-05-16, parallel)

### Turing (CTO) — approve-with-changes
- **Top concern**: YAML config is premature; ship CLI-only v1 (7/9 candidate fields are CLI-trivial). → **Accepted**: v1 has no YAML; flag inventory above.
- **Convention inconsistency**: Python source layer uses `in_` (keyword-safe), scripts use `india`. → **Accepted dual-layer**: source path stays `factorlab.countries.in_.*`; scripts get the operator-friendly `india_*` prefix. Doc note added.
- **Push for "cut the rename"**: rejected — user explicitly asked for naming consistency in the script layer.
- **Push for keeping auth_server in `scripts/in_/`**: *initially* rejected in favour of flat layout — but user overrode (see correction note at top of doc). All 4 India scripts live in `scripts/in/` (no underscore — `in_` was needed only for the Python source layer where `in` is a keyword). Turing was right to keep the country grouping; my flat-layout argument was wrong.

### Ritchie (OS / scripts) — approve-with-changes
- **Procfile/railway gunicorn module name** not updated by initial plan → **Accepted**: step 4 now explicitly renames `auth_server:app` → `india_equities_upstox_auth_server:app`.
- **`schtasks /create /f` doesn't overwrite renamed tasks** → **Accepted**: step 8 now `/delete`s old IDs explicitly before `/create`.
- **3 not 4 scripts** need depth fix (4th doesn't exist yet) → **Accepted**: step 2 wording corrected.
- **No path-with-spaces breakage** in the existing schtasks quoting after rename — confirmed.

### Heimdall (security) — approve-with-changes
- **OAuth redirect URI**: confirmed **not** path-coupled. `/callback` route is env-driven via `UPSTOX_REDIRECT_URL`. Rename is safe. No action.
- **`parents[N]` silent breakage**: → **Accepted**: every renamed script gets `assert (PROJECT_ROOT / "pyproject.toml").exists(), f"PROJECT_ROOT misresolved: {PROJECT_ROOT}"` so wrong-depth fails loudly.
- **Verify exactly one Flask app** under flat `scripts/` after flatten — **Accepted** as new step 4b.
- **Procfile/railway/filename leakage**: not meaningful uplift; skip.
- **Historical YAML config secret-hygiene**: moot — v1 has no YAML.

### factorlab-pm (delivery) — approve-with-changes
- **Done-tests too vague**: → **Accepted**: every OoO row sharpened above (specific exit codes, row counts, grep return values).
- **Sequencing**: 008 before 007 Phase 2 (otherwise Phase 2 registers tasks at old paths). → **Accepted**.
- **Scope creep watch** on `_maintenance/` mention → **Accepted**: deferred; not in this round.
- **Owner consolidation**: ritchie can drive steps 2–6 + 8 sequentially in one operator session. → noted.

---

## Open follow-ups (deferred, not blocking)

- US script rename (same convention, after India is green) — separate doc.
- Political script rename (8 vendors; bigger surface) — separate doc.
- Adding `--config` support to the historical script — only if the same flag-set is invoked twice in real use.
- Rolling the new notify metadata into existing backfill scripts during the US rename pass.
