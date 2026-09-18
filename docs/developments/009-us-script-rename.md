# 009 — Apply 008 naming convention to US scripts

> Status: `[accepted]` — drafted + executed 2026-05-16. Follows [008](008-script-naming-india-historical.md) which established the `{country}/{domain}/{vendor}/{file}.py` layout + `{country}_{domain}_{vendor}_{action}.py` filename convention for India. No fresh design decisions — this is mechanical application to US.

## Rename map

### US equities (5 files → 3 vendor subfolders)

| Current                                  | New                                                                |
|------------------------------------------|--------------------------------------------------------------------|
| `scripts/us/equities/auth.py`            | `scripts/us/equities/schwab/us_equities_schwab_auth.py`            |
| `scripts/us/equities/live.py`            | `scripts/us/equities/schwab/us_equities_schwab_live.py`            |
| `scripts/us/equities/backfill.py`        | `scripts/us/equities/schwab/us_equities_schwab_historical.py`      |
| `scripts/us/equities/daily.py`           | `scripts/us/equities/eodhd/us_equities_eodhd_daily.py`             |
| `scripts/us/equities/universe.py`        | `scripts/us/equities/blackrock/us_equities_blackrock_universe.py`  |

**Action rename**: Schwab `backfill.py` → `..._historical.py` matches India naming + describes "simple one-shot historical pull" more accurately than the gap-fill connotation of "backfill". Political keeps the `backfill` action because it's a phased multi-source operation (distinct shape).

**Vendor for universe**: BlackRock IWV CSV is the data source for the Russell 3000 universe builder. Vendor = `blackrock`. Folder feels heavy for one script but follows the convention rule strictly.

### US political (5 orchestrators + 1 vendor-specific + 1 maintenance move)

| Current                                                  | New                                                                                |
|----------------------------------------------------------|------------------------------------------------------------------------------------|
| `scripts/us/political/daily.py`                          | `scripts/us/political/us_political_daily.py`                                       |
| `scripts/us/political/backfill.py`                       | `scripts/us/political/us_political_backfill.py`                                    |
| `scripts/us/political/status.py`                         | `scripts/us/political/us_political_status.py`                                      |
| `scripts/us/political/verify.py`                         | `scripts/us/political/us_political_verify.py`                                      |
| `scripts/us/political/load_house_clerk_historical.py`    | `scripts/us/political/house_clerk/us_political_house_clerk_historical.py`          |
| `scripts/us/political/refresh_bioguide.py`               | `scripts/us/political/_maintenance/us_political_refresh_bioguide.py`               |
| `scripts/us/political/_maintenance/*.py`                 | (stay — ad-hoc one-shots, no rename)                                               |

**Multi-vendor orchestrators skip the vendor segment** in both folder and filename. Mirrors `factorlab.countries.us.political.orchestrator` which is a flat module, not a vendor subpackage. Filename pattern relaxes to `{country}_{domain}_{action}.py` when there's no single vendor.

`refresh_bioguide.py` moves to `_maintenance/` — manual-only, post-matcher-edit rebuild of the bioguide cache. Already classified as `maintenance` in the ingestion inventory.

## Task Scheduler entry IDs

| Old ID                              | New ID                                  |
|-------------------------------------|-----------------------------------------|
| `FactorLab-Schwab-AuthRefresh`      | `FactorLab-USEquities-Schwab-AuthRefresh` |
| `FactorLab-US-EquitiesLive`         | `FactorLab-USEquities-Schwab-Live`      |
| `FactorLab-US-EquitiesDaily`        | `FactorLab-USEquities-EODHD-Daily`      |
| `FactorLab-Political-Daily`         | `FactorLab-USPolitical-Daily`           |
| `FactorLab-Political-Weekly`        | `FactorLab-USPolitical-Weekly`          |
| `FactorLab-Political-Hourly`        | `FactorLab-USPolitical-Hourly`          |

(Same `schtasks /delete` then `/create` pattern as 008.)

## Path adjustments

- 5 equities scripts: `parents[3] → parents[4]` (now one folder deeper inside `{vendor}/`)
- 4 political orchestrators (`daily/backfill/status/verify`): `parents[3]` unchanged (stay at the domain folder root)
- `house_clerk/us_political_house_clerk_historical.py`: `parents[3] → parents[4]`
- `_maintenance/us_political_refresh_bioguide.py`: `parents[3] → parents[4]`
- Other `_maintenance/*.py`: already at `parents[4]` (no change)
- `PROJECT_ROOT` assertion added to every renamed script.

## Out of scope (deferred)

- Notification rollout to the renamed backfill / daily scripts (US daily is still the EODHD stub from 007 Phase 5 — full rebuild is queued).
- `daily.py` rebuild — still the 4-ticker EODHD stub. Phase 5 of [007](007-getting-live-again.md) replaces it with the real Russell-3000 post-close orchestrator.
- The actual `schtasks /delete /tn` + `/create` execution — requires elevated PowerShell on operator's machine.
