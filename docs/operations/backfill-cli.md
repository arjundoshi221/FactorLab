# `factlab_backfill` — one CLI for every source's historical fills

**Owner:** turing (protocol) · per-source owner (implementation) · **Last updated:** 2026-05-15

## Usage

```powershell
# What's registered?
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" scripts\_shared\factlab_backfill.py --list

# Preview a backfill (no writes)
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" scripts\_shared\factlab_backfill.py `
    --source eodhd --since 2024-01-01 --until 2024-01-31 --dry-run

# Run it
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" scripts\_shared\factlab_backfill.py `
    --source eodhd --since 2024-01-01 --until 2024-01-31

# Capture machine-readable report (JSON to stdout)
... --source eodhd --json > eodhd-report.json
```

## Flags

| Flag         | Meaning                                                                                         |
|--------------|-------------------------------------------------------------------------------------------------|
| `--source`   | Source code (required unless `--list`). Use `--list` to see what's registered.                 |
| `--since`    | Inclusive start date `YYYY-MM-DD`. Interpretation is source-specific (vendor-side semantics).  |
| `--until`    | Inclusive end date `YYYY-MM-DD`.                                                                |
| `--dry-run`  | (Default off) Print the plan and exit. No external calls, no DB writes.                         |
| `--list`     | Print registered Backfillers and exit.                                                          |
| `--json`     | Emit final `BackfillReport` as JSON on stdout (still also logged).                              |

## Registered sources

As of 2026-05-15:

| Source code | Backfiller class                                                       | Driver script (legacy, still functional) |
|-------------|------------------------------------------------------------------------|------------------------------------------|
| `eodhd`     | `factorlab.countries.us.equities.eodhd.backfill.EODHDBackfiller`       | `scripts/us/equities/eodhd/us_equities_eodhd_daily.py` for live operation; dispatcher wraps the same client for backfill |

Sources that have working drivers but **not yet wrapped** under the `Backfiller` protocol (these continue to function as standalone CLIs — wrap them when their per-source quirks settle):

- `scripts/us/equities/schwab/us_equities_schwab_historical.py` — Schwab historical via `ThreadPoolExecutor`
- `scripts/us/political/us_political_backfill.py` — 4-phase political orchestrator (legislators → trades → conjunctions → verify)
- `scripts/us/political/house_clerk/us_political_house_clerk_historical.py` — House Clerk PTR historical with the 4-tier classifier
- (No Upstox historical backfill exists yet)

## Adding a new Backfiller

1. Create `src/factorlab/countries/<country>/<domain>/<vendor>/backfill.py` defining a class with `plan()` and `run()` (see `Backfiller` protocol in `factorlab.shared.ingest.backfill`).
2. At module bottom, call `register_backfiller(source, instance)`.
3. Add `import factorlab.countries.<...>.backfill` to the registrations block in `scripts/_shared/factlab_backfill.py`.

The protocol enforces nothing source-specific — your Backfiller can use whatever concurrency / state / classifier model suits the data. The dispatcher only cares about `plan()` returning a `BackfillPlan` and `run()` returning a `BackfillReport`.

## Failure path

- Per-unit error: `notify_fn(...)` is called inside `run()` with `severity='warn'`; the report's `errors` list captures the message; `units_failed` increments.
- Overall failure: dispatcher returns `ExitCode.WARN` (some units failed) or `ExitCode.FATAL` (no Backfiller registered, bad date, etc.).
- Uncaught exception: `supervised(...)` at the script tail catches everything → `ExitCode.CRASH`.

## Resume semantics

The protocol does not mandate resumability — each Backfiller chooses. Recommended pattern (see `factorlab.shared.ingest.state.State`):

```python
state = State(source="eodhd")
for unit in plan.units:
    key = f"{unit['symbol']}/{unit['from_date']}"
    if state.is_done(key):
        continue
    process(unit)
    state.mark_done(key, flush=True)
```

State checkpoints live under `data/_state/<source>/state.json` (or `data/<namespace>/_state/<source>.json` for legacy nested layouts).
