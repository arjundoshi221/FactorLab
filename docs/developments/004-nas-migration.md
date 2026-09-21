# 004 — Raw vendor data backed up to NAS

**Status:** `[in-progress]` — initial sync running 2026-05-13 · **Owner:** ritchie + factorlab-pm · **Security review:** heimdall

## Decision

The repo (`<repo>/data/<source>/raw/`) is the **canonical / primary** location for raw vendor dumps. `E:\NAS\factorlab\raw\` is an **additive-only backup mirror** — populated by a one-way sync script, never read from by application code.

Changed from the earlier "migrate-and-flip" model after explicit user direction: *"I want a backup available in the current form if the NAS is not available. Then load it in the current directory. Then you want a script that can basically 'Sync' NAS which is basically pushing raw backups to the NAS — no copies on this."*

## What this means in practice

- `factorlab.shared.paths.raw_dir(source)` continues to default to `<repo>/data/...`. **`FACTORLAB_RAW_ROOT` is not flipped.**
- The application keeps working if NAS is unavailable / disconnected / corrupted — code never reads from NAS.
- A scheduled sync (manual or Task Scheduler) pushes new files from repo → NAS. Deletes are NOT mirrored by default (a backup that propagates deletes is a footgun).
- A new env var `FACTORLAB_NAS_BACKUP_ROOT` configures the backup destination (default behaviour: must be passed via `--dest`). Distinct from `FACTORLAB_RAW_ROOT` so an operator can't accidentally relocate the working data set.

## Implementation

Script: [`scripts/_shared/sync_raw_to_nas.py`](../../scripts/_shared/sync_raw_to_nas.py)

| Mode         | Effect                                                                  |
|--------------|-------------------------------------------------------------------------|
| `--dry-run`  | (default) walk source; report per-file copy plan; no writes             |
| `--sync`     | per-file copy when dest is missing or older (mtime + 1s tolerance)      |
| `--verify`   | sha256 a 1% random sample on both sides; non-zero exit on mismatch      |
| `--mirror`   | additionally delete dest-only files (destructive; off by default)       |

Source/dest mapping (hardcoded in `SOURCES`):

| Source (in repo)            | Dest (NAS)                                |
|-----------------------------|-------------------------------------------|
| `data/political/raw`        | `E:\NAS\factorlab\raw\political\raw`      |
| `data/eodhd`                | `E:\NAS\factorlab\raw\eodhd`              |
| `data/in/live`              | `E:\NAS\factorlab\raw\in\live`            |
| `data/upstox/instruments`   | `E:\NAS\factorlab\raw\upstox\instruments` |

After every `--sync`, the script writes `<dest>/_LAST_SYNC.txt` with timestamp + counts.

## Initial-sync result (2026-05-13)

Dry-run baseline (pre-sync):

| Source                       | Files       | Size      |
|------------------------------|-------------|-----------|
| `data/political/raw`         | 311,169     | 37.5 GB   |
| `data/eodhd`                 | 9           | 2.2 MB    |
| `data/in/live`               | 1,706       | 19.2 MB   |
| `data/upstox/instruments`    | 3           | 69.1 MB   |
| **TOTAL**                    | **312,887** | **37.6 GB** |

E:\ free space at start: 1.5 TB (plenty of headroom).

The initial sync (running 2026-05-13 22:26 UTC) is the longest run; subsequent runs only touch new / changed files.

## Operational cadence

- **Run on demand** before major refactors: `python scripts/_shared/sync_raw_to_nas.py --dest E:/NAS/factorlab/raw --sync`.
- **Weekly Task Scheduler entry** (Phase 7 of the restructure plan adds this). Suggested: Sundays 03:00, run `--sync` then `--verify`. Send `notify(severity='warn')` on non-zero exit.
- **Never `--mirror`** in the scheduled run. Reserve mirror for explicit operator-initiated cleanup with a manual review of what would be deleted.

## Rationale

1. **NAS-down-friendly.** If `E:\NAS\` is unmounted, disconnected, or restored from an older snapshot, FactorLab keeps running because nothing reads from NAS at runtime.
2. **OneDrive throttle.** `data/political/raw/fec/` has ~33K small JSON files that periodically choke OneDrive indexing; the in-repo retry loop in `_state.py:55-72` is the workaround. A weekly backup off the OneDrive-synced disk gives us a recovery point.
3. **Defensive against operator error.** Additive-only sync means accidentally clearing `<repo>/data/eodhd/` doesn't propagate to NAS, so a restore is trivial.
4. **Cheap.** First sync is ~38 GB; subsequent runs touch only new fetches. Likely a few hundred MB per week.

## What's already shipped

- [`scripts/_shared/sync_raw_to_nas.py`](../../scripts/_shared/sync_raw_to_nas.py) — three modes (`--dry-run`, `--sync`, `--verify`) plus `--mirror`.
- [`src/factorlab/shared/paths.py`](../../src/factorlab/shared/paths.py) — env-knobbed roots; **default = `<repo>/data` (unchanged operational behaviour)**.
- [`scripts/_check_paths_in_code.py`](../../scripts/_check_paths_in_code.py) — pre-commit lint refusing new hardcoded `"data/.../raw"` literals.
- 20 unit tests pinning the legacy layout contract (`tests/shared/test_paths.py`).
- Project runbook + security review under `E:\AGENTS\memory\projects\factorlab\docs\`.

## Open follow-ups

1. **Scheduled weekly sync** (Task Scheduler entry) — Phase 7 deliverable.
2. **Quarterly mirror** to a second physical disk (cold archive). Out of scope for v1.
3. **NAS ACL check** — `Get-Acl E:\NAS\factorlab\raw | Format-List` should restrict to user. Operator confirms after initial sync.

## What this is explicitly NOT doing

- Moving tokens, state, heartbeats, or logs to NAS. Those stay in repo.
- Flipping `FACTORLAB_RAW_ROOT` to NAS. Repo remains canonical for the application.
- Deleting in-repo copies. The repo is canonical — there's nothing to delete.
- Adding a NAS backup automation. Manual / scheduled sync is the v1 model.
- Adding a restore-from-NAS-to-repo script. Direct `robocopy` or re-run the sync after restoring an empty repo (the script copies missing dest files; trivial to invert for emergency restore).

## When this lands

Status → `[shipped]` once: (1) initial `--sync` completes, (2) `--verify` passes, (3) a Phase 7 scheduled weekly entry is wired in `infra/os/windows/scheduled-jobs.md`.
