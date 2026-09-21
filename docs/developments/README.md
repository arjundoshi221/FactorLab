# FactorLab — Developments

Forward-looking architecture decisions and development plans live here.

## Purpose

This folder is **distinct** from the rest of `docs/`:

| Folder | Holds |
|---|---|
| `docs/architecture/` | Current-state system spec |
| `docs/data-sources/` | Per-vendor integration spec (live behaviour) |
| `docs/countries/` | Per-market spec |
| `docs/operations/` | Runbooks, schedules, failure modes |
| **`docs/developments/`** | **Decisions about what we are *about to* build, and the rationale** |

When a development plan ships, the relevant pieces migrate into the folders above and the `developments/` doc is updated with the date it landed and a pointer. Old plans are not deleted — they form an audit trail of architectural reasoning.

## Conventions

- **Numbered, append-only**: `001-...`, `002-...`. Numbers are never reused.
- **One decision per file.** If a doc grows multiple decisions, split it.
- **Status tag at the top** of every doc:
  - `[proposed]` — drafted, not yet approved
  - `[accepted]` — decision made, implementation pending
  - `[in-progress]` — partially shipped
  - `[shipped]` — fully landed; doc is now historical reference, point to current-state docs
  - `[superseded by NNN]` — replaced by a later development
- **Always include**: the decision, the rationale, the tradeoffs considered, the things explicitly *not* being done, and any open questions still owed.
- Cross-link to current-state docs (`docs/architecture/...`, etc.) rather than duplicating spec.

## Index

| # | Title | Status |
|---|---|---|
| [001](001-local-docker-canonical-db.md) | Local Docker as canonical database; Railway as auth-only | `[accepted]` |
| [002](002-live-us-market-data.md) | Live US market data via Schwab streaming + REST tier | `[accepted]` |
| [003](003-sectoral-political-analytics.md) | Sectoral political-analytics layer (GICS rollups in `derived` schema) | `[proposed]` |
| [004](004-nas-migration.md) | Raw vendor data on NAS (`E:\NAS\factorlab\raw\`) — script + dry-run shipped; apply deferred | `[proposed]` |
| [005](005-commodities-squeeze-signals.md) | Commodity squeeze-signal sleeve (AU wool first, then iron ore / LNG / lithium / grains) | `[proposed]` |
| [006](006-pre-commit-enforcement.md) | Pre-commit enforcement — make `.pre-commit-config.yaml` bite | `[proposed]` |
| [007](007-getting-live-again.md) | Getting live again — phased re-enable of notifications, India, political, US daily, US live | `[in-progress]` |
| [008](008-script-naming-india-historical.md) | Script naming convention (`{country}_{domain}_{vendor}_{action}.py`) + India historical build-out | `[accepted]` |
| [009](009-us-script-rename.md) | Apply 008 convention to US scripts (equities + political) | `[accepted]` |
| [010](010-hyperliquid-tokenized-equities.md) | Hyperliquid tokenized-equity perps as 24/7 after-hours signal source (`alt_crypto_perp`) | `[proposed]` |
