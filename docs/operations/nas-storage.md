# NAS storage — `E:\NAS\factorlab\` (additive backup mirror)

**Owner:** ritchie · **Security review:** heimdall · **Last updated:** 2026-05-13

## Model in one line

The repo is **primary**. NAS is a **one-way additive backup mirror** populated by [`scripts/_shared/sync_raw_to_nas.py`](../../scripts/_shared/sync_raw_to_nas.py). Application code never reads from NAS — if NAS is unavailable, FactorLab keeps working.

## What lives where

```
<repo>/data/                              ← PRIMARY (code reads/writes here)
├── political/raw/        ────────────►   E:\NAS\factorlab\raw\political\raw\
├── eodhd/                ────────────►   E:\NAS\factorlab\raw\eodhd\
├── in/live/              ────────────►   E:\NAS\factorlab\raw\in\live\
└── upstox/instruments/   ────────────►   E:\NAS\factorlab\raw\upstox\instruments\

<repo>/data/_tokens/                      ← repo-only (not backed up by sync)
<repo>/data/_state/                       ← repo-only
<repo>/data/_heartbeat/                   ← repo-only
<repo>/logs/                              ← repo-only
```

Tokens / state / heartbeats / logs are intentionally out of scope for the sync — they're small, frequently rewritten, and don't benefit from a cold mirror.

## How code reads paths

```python
from factorlab.shared.paths import raw_dir

CACHE_DIR = raw_dir("political")           # → <repo>/data/political/raw
EODHD_DIR = raw_dir("eodhd")               # → <repo>/data/eodhd
LIVE_DIR  = raw_dir("upstox_live")         # → <repo>/data/in/live
```

`FACTORLAB_RAW_ROOT` defaults to `<repo>/data` and is **not** flipped to the NAS path. The NAS location is controlled by a separate `FACTORLAB_NAS_BACKUP_ROOT` env var (or `--dest` CLI flag) that only the sync script reads.

## Sync script

```powershell
# 1. Preview (default — safe)
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" `
    scripts\_shared\sync_raw_to_nas.py `
    --dest E:\NAS\factorlab\raw --dry-run

# 2. Push new / changed files (additive only)
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" `
    scripts\_shared\sync_raw_to_nas.py `
    --dest E:\NAS\factorlab\raw --sync

# 3. Spot-check (sha256 1% random sample on both sides)
"C:\Users\arjd2\.conda\envs\factorlab\python.exe" `
    scripts\_shared\sync_raw_to_nas.py `
    --dest E:\NAS\factorlab\raw --verify
```

The script writes `E:\NAS\factorlab\raw\_LAST_SYNC.txt` after every `--sync` with timestamp + counts so future-you knows when the last refresh ran.

### Mirror mode

`--mirror` makes the sync destructive — files at NAS that are absent from the repo source get **deleted**. Off by default. Use only for operator-initiated cleanup after explicit review. Never include in a scheduled run.

### Schedule (Phase 7 of restructure)

Recommended Task Scheduler entry:

| Name                              | Schedule         | Action                                                                  |
|-----------------------------------|------------------|-------------------------------------------------------------------------|
| `FactorLab-NAS-Sync-Weekly`       | Sunday 03:00     | `python scripts/_shared/sync_raw_to_nas.py --dest E:/NAS/... --sync`    |
| `FactorLab-NAS-Verify-Weekly`     | Sunday 04:00     | `python scripts/_shared/sync_raw_to_nas.py --dest E:/NAS/... --verify`  |

On non-zero exit, fire `notify(severity='warn', source='nas_sync', ...)`. Wired alongside the rest of Task Scheduler in Phase 7.

## Disaster recovery

The NAS is **not** canonical. Postgres on Railway is. NAS holds raw vendor dumps (PDFs, JSON responses, parquet/arrow caches) that can in principle be re-fetched. But re-fetching can be slow / rate-limited (FEC at 1k/hr, Senate eFD geo-blocked from cloud), so:

### Repo data lost, NAS intact

```powershell
# Mirror NAS → repo (the sync script copies repo → NAS one-way; for the
# reverse, robocopy is the simplest tool)
robocopy E:\NAS\factorlab\raw\political\raw <repo>\data\political\raw /E
robocopy E:\NAS\factorlab\raw\eodhd          <repo>\data\eodhd          /E
robocopy E:\NAS\factorlab\raw\in\live        <repo>\data\in\live        /E
robocopy E:\NAS\factorlab\raw\upstox\instruments <repo>\data\upstox\instruments /E
```

### NAS lost, repo intact

Re-create `E:\NAS\factorlab\raw\` and run `--sync` from scratch. Same as first-time initial sync.

### Both lost

Backfill from vendors via `scripts/us/political/us_political_backfill.py` and the equities backfill scripts. Slow but deterministic — that's why we have the backfillers.

## Security posture (heimdall)

- `assert_under_root()` in `paths.py` refuses any resolved path that escapes the configured root. Defends against `..`-injection.
- `E:\NAS\factorlab\` should restrict to the user account. No anonymous share access. Check:
  ```powershell
  Get-Acl E:\NAS\factorlab\raw | Format-List
  ```
- `_LAST_SYNC.txt` contains no sensitive info.
- Sync script `--mirror` flag is OFF by default; destructive operations require explicit operator intent.

## Common operations

**See what's on NAS today:**
```powershell
Get-ChildItem -Recurse E:\NAS\factorlab\raw | Measure-Object -Sum Length
```

**Last sync time:**
```powershell
Get-Content E:\NAS\factorlab\raw\_LAST_SYNC.txt
```

**Spot-check a specific file present in both:**
```powershell
Test-Path <repo>\data\eodhd\AAPL.US_daily.parquet
Test-Path E:\NAS\factorlab\raw\eodhd\AAPL.US_daily.parquet
```

**Cancel a sync mid-run:** Ctrl+C. The script copies file-by-file and is interrupt-safe — partially-copied files are overwritten on the next run via mtime comparison.
