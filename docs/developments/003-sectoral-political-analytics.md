# 003 — Sectoral political-analytics layer

> Status: `[proposed]` — drafted 2026-05-08. Depends on the political ingest sleeve being converged (FEC + Congress.gov + LDA all current as of 2026-05-08).

## Decision

Build a sector-level (GICS) rollup of every political signal stream — donations, lobbying, contracts, legislator trades, bill activity, and committee jurisdiction — into a single `derived.sector_political_activity_monthly` materialized view, with a wide composite `derived.sector_political_score_monthly` on top. Six implementation phases, ship-after-each.

Raw fact tables (`alt_political_us.*`) stay faithful to source. All sector classification happens in `derived` via dedicated mapping tables — never written back into the raw schemas.

## Why

Political alpha is most legible at the sector level (Defense PACs → Defense bills → Defense contracts → Defense PTRs → Defense stocks). Single-name attribution is dominated by noise. Five fact tables loaded; none currently joinable to GICS.

Concrete coverage gaps as of 2026-05-08:

| Layer | Status |
|---|---|
| `instruments.gics_sector_id` | exists (mig 026), **NULL across all 4,732 political-relevant tickers** |
| `lobbying_filings.client_ticker` | resolved on **15%** of 1.05M filings; remaining 85% are trade associations with no ticker path to a sector |
| `bills.policy_area` | populated on 52,576 bills; **no `policy_area → GICS` mapping exists** |
| `committee_sector_map` | 21 hand-seeded rows (mig 008), **no code reads it** |
| `derived` schema | empty — reserved but unused |

Full prior context: [memory/project_sectoral_political_analytics.md](https://github.com/) (origin 2026-05-03, expanded 2026-05-08 with point #7 for trade-association recovery).

## Architecture

### Mapping tables

| Table | Purpose | Cardinality |
|---|---|---|
| `alt_political_us.lobby_client_sector_map` (new) | Tickerless trade associations + private giants → GICS | ~1,000 entries |
| `alt_political_us.policy_area_sector_map` (new) | All 33 distinct `bills.policy_area` values → GICS, with `weight` for multi-sector splits | ~40 rows (≥1 per source value) |
| `alt_political_us.committee_sector_map` (existing, mig 008) | Senate/House committee → GICS | 21 hand-curated rows |
| `ref.instruments.gics_sector_id` (existing FK, NULL) | Ticker → GICS sector | 4,732 political-relevant tickers backfilled via EODHD |

`gics_code` is text (matches existing `committee_sector_map.gics_code` convention) — supports `'*'` for all-sector and 4-digit industry-group codes alongside 2-digit sector codes. Resolution from `gics_code` text → `gics_sectors.id` happens in the materialized-view query.

### Resolver extension

Single new method on existing [_resolver.py](../../src/factorlab/sources/political/_resolver.py):

```
Resolver.resolve_to_sector(name, kind='auto') → SectorResolution(ticker, gics_codes, method, confidence)
```

Cascade: existing `resolve()` → ticker→sector cache → name→`lobby_client_sector_map` → None. `engine` parameter added to `__init__` (optional, back-compat preserved for the one existing no-arg caller in `_asset_classifier.py:191`).

### Derived materialized views

`derived.sector_political_activity_monthly` is **long-format** with an `attribution_method` discriminator (not separate-column-per-bucket). 8-leg `UNION ALL`:

| leg | source | join path | fact_kind | attribution_method |
|---|---|---|---|---|
| 1 | `campaign_donations` (Mode A corp PAC) | → `fec_committees.sponsor_company_ticker` → `instruments` → `gics_sectors` | donation | ticker_direct |
| 2 | `campaign_donations` (Mode B individual) | → `donor_employer` → `lobby_client_sector_map` or `lobby_client_aliases.ticker` → `instruments` | donation | trade_assoc / ticker_direct |
| 3 | `lobbying_filings` (`client_ticker NOT NULL`) | → `instruments` → `gics_sectors` | lobbying | ticker_direct |
| 4 | `lobbying_filings` (`client_ticker IS NULL`) | → `lobby_client_sector_map` on normalized client_name | lobbying | trade_assoc |
| 5 | `gov_contracts` (`ticker NOT NULL`) | → `instruments` → `gics_sectors` | contract | ticker_direct |
| 6 | `legislator_trades_dedup` (`ticker NOT NULL`) | → `instruments` → `gics_sectors` | ptr | ticker_direct |
| 7 | `bills` | → `policy_area_sector_map` (with `weight`) | bill | policy_area_mapped |
| 8 | `bill_committees` | → `committee_sector_map` → `gics_sectors` | committee_signal | committee_jurisdiction |

Every leg also produces an `__UNATTRIBUTED__` sector row for residuals. **Reconciliation invariant**: `SUM(dollar_amount)` per `fact_kind` matches source-table sum exactly. Auditable per fact_kind.

`derived.sector_political_score_monthly` pivots wide per `(sector_code, month)` with per-fact-kind dollar columns plus z-scored `composite_z`.

## Implementation phases

| # | Ships | Migration | Independent? |
|---|---|---|---|
| **G1** | `lobby_client_sector_map` table + ~1,000-entry YAML | 029 | yes |
| **G2** | `policy_area_sector_map` + 33-source-value YAML | 030 | yes (parallel with G1) |
| **G3** | `instruments.gics_sector_id` backfill via EODHD `/fundamentals/{symbol}` for 4,732 political-relevant tickers + manual overrides YAML | 031 | yes (parallel with G1, G2) |
| **G4** | Resolver `resolve_to_sector()` extension + tests | none | needs G1 + G3 |
| **G5** | `derived.sector_political_activity_monthly` + `derived.sector_political_score_monthly` | 032 | needs G1, G2, G3 |
| **G6** | Orchestrator `run_phase_5` wiring + `--phase 5` driver flag | none | needs G5 |

Migration head verified at 028 as of 2026-05-08; 029-032 reserved.

### Phase G1 — preflight finding (drives sizing)

Top-500 unresolved by spend covers only **47%** of unattributed lobbying dollars. Top-1,000 hits ~60%. Of the top-30 unresolved by spend, ~70% are pure trade associations (Chamber of Commerce $914M, NAR $794M, PhRMA $383M, AMA, BR, AHA, …) and ~30% are corporate-alias gaps (`"RTX CORPORATION AND AFFILIATES"`, `"DOW CHEMICAL COMPANY DBA DOW"`, `"CHEVRON U.S.A. INC."`).

**Triage step**: a one-shot `scripts/political/triage_lobby_unresolved.py` splits the top-1,500 unresolved into two YAML targets:
- corporate-alias variants → append to `configs/reference/contractor_aliases.yaml`
- true trade associations + tickerless private giants → seed `configs/reference/lobby_client_sector_map.yaml`

Trade associations that lobby across all sectors (Chamber of Commerce, BR) get `gics_code='*'` with `signal_strength='extreme'` — the materialized view explodes `'*'` into all 11 sectors at SQL time.

### Phase G2 — confirmed cardinality

33 distinct `bills.policy_area` values, sized:

- **Single-sector (~25)**: Health→35, Energy→10, Finance and Financial Sector→40, Transportation and Public Works→20, Agriculture and Food→30, Armed Forces and National Security→201010, etc.
- **Multi-sector splits (~5, with `weight` < 1.00)**:
  - "Science, Technology, Communications" → 45 (0.50) + 50 (0.50)
  - "Commerce" → 25 (0.40) + 30 (0.30) + 40 (0.30)
  - "Foreign Trade and International Finance" → 40 (0.50) + 25 (0.50)
  - "Public Lands and Natural Resources" → 10 (0.50) + 15 (0.50)
- **`'*'` / non-corporate (~3)**: "Government Operations and Politics" (3,786 bills, structurally cross-sector) → `'*'` with low signal_strength. "Private Legislation", "Social Sciences and History", "Sports and Recreation" → `'*'` with `signal_strength='low'`.

Hard gate: `SELECT b.policy_area FROM bills b WHERE b.policy_area IS NOT NULL EXCEPT SELECT policy_area FROM policy_area_sector_map` returns 0 rows.

### Phase G3 — EODHD cost

4,732 distinct political-relevant tickers (union of `legislator_trades.ticker`, `gov_contracts.ticker`, `lobbying_filings.client_ticker`, `fec_committees.sponsor_company_ticker`). EODHD `/fundamentals/{symbol}` costs 10 credits per call → **47,320 credits one-shot**.

Sector-only in v1. EODHD `General.GicSector` returns the **name** ("Industrials"), not code — name-keyed lookup against `ref.gics_sectors.name` resolves it. `gics_industry_id` deferred — EODHD's `GicIndustry` returns sub-industry names that don't cleanly join to our 4-digit groups.

Manual overrides YAML for tickers EODHD misses (delisted, foreign-primary listed). ~50-200 entries expected.

### Phase G5 — view shape rationale

Long-format with `attribution_method` discriminator chosen over wide-per-bucket because:
1. Adding a 4th attribution method later (e.g. `industry_keyword_classified`) is a row addition, not a column addition + view rewrite
2. Coverage triage SQL is a single `GROUP BY (fact_kind, attribution_method)`
3. Serializes cleanly to parquet for offline analysis
4. Pivot to wide for the score view is a single CTE

UNIQUE index `(sector_code, month, fact_kind, attribution_method)` enables `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

## Things explicitly NOT being done

- **`gics_industry_id` backfill** — sector-only in v1. Industry granularity is a +2-week scope expansion if needed.
- **Mode B donor_employer full attribution** — 1M+ distinct freeform employer strings. v1 only attributes employer rows that hit `lobby_client_aliases` or `lobby_client_sector_map` directly. Mode B coverage will be 20-30% in v1; Mode A corp-PAC donations carry the sharper signal.
- **Auto-growth of sector maps** — sector tags are curated artifacts, never written by ingest paths. Distinct from `lobby_client_aliases` and `contract_aliases` which ARE auto-grown by ingest.
- **Cross-country sector layer** — `country_code` is in every PK but only `'US'` populated in v1. EU/JP/HK political data isn't loaded, so sector mapping for those is deferred to whenever those sleeves come online.
- **Foreign companies without ADRs** — they live in `__UNATTRIBUTED__` forever. Same for individual lobbyists, single-bill LLCs, NGOs/think tanks without sector clean-mapping, foreign governments.
- **PTR amount precision** — `legislator_trades.amount_mid` is the band midpoint (STOCK Act discloses ranges, not exact dollars). Acceptable for monthly aggregate; will be flagged in any methodology doc derived from these views.

## Open questions owed to user

1. **EODHD credit budget** — 47k credits one-shot for G3. Confirm plan capacity. Fallback: hand-curate top-500 political tickers via YAML, skip EODHD pull, accept lower coverage.
2. **Mode B donor_employer scope** — confirm OK to ship low-coverage (~20-30%) Mode B attribution in v1, with expansion in a later cycle.
3. **"Government Operations and Politics" mapping** — `'*'` with low signal-strength (recommended) or exclude entirely.

## Verification gates

| Phase | Gate |
|---|---|
| G1 | ≥ 60% of unattributed lobbying spend covered by `lobby_client_sector_map` join |
| G2 | Zero unmapped `policy_area` values in `bills` |
| G3 | ≥ 90% sector coverage on the 4,732-ticker political subset |
| G4 | Three named test cases pass (PhRMA → trade_assoc, Lockheed → ticker_direct, unknown → none) |
| G5 | Per-fact_kind dollar reconciliation matches source tables exactly |
| G6 | `python scripts/us/political/us_political_backfill.py --phase 5` succeeds end-to-end and is idempotent |

## When this ships

When G1-G6 complete, this doc moves to `[shipped]` and pointers go to:
- `docs/architecture/derived-schema.md` (current-state spec for the `derived` schema, to be created)
- `docs/data-sources/political/senator-trades.md` (existing, gets a new section linking to the sector-rollup outputs)
- `docs/operations/political-orchestrator.md` (existing, gets a new "Phase 5: derived refresh" entry)

Working plan with full file-by-file detail: [~/.claude/plans/greedy-foraging-wand.md](file:///C:/Users/arjd2/.claude/plans/greedy-foraging-wand.md) (local-only, gitignored).
