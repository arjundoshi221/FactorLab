# Political Signals — US Congressional Trades

> Status: `[live, post-redesign]` · Last updated: 2026-05-03 · Verified empirically end-to-end against live sources

## Schema redesign 2026-05-01 — at a glance

The `alt_political` schema was renamed and restructured per
[`docs/architecture/database.md`](../../architecture/database.md).
Key changes for operators:

- **Schema rename**: `alt_political` → `alt_political_us`. India political data
  will land in a future `alt_political_in`. Reference dims stay shared in `ref`.
- **Audit unification**: the per-schema `raw_archive` table moved to
  `audit.raw_archive` (single table for ALL sources — political, market,
  future alt_*). Every row has an `endpoint_id` FK identifying the feed.
- **Vendor model**: freeform `source` String columns replaced with `endpoint_id`
  Int FK to `ref.data_endpoints` (which itself FKs to `ref.vendors`).
  `legislator_trades.source = 'house_clerk_ptr'` is now `endpoint_id = <int>`,
  reached via `JOIN ref.data_endpoints e ON e.id = lt.endpoint_id`.
- **`legislator_aliases` table**: manual override curation. The Van Taylor →
  T000479 mapping that previously lived in a Python script is now a database
  row. The bioguide matcher consults this table as Stage 0 (highest priority).
- **Strict-NULL across the board**: `transaction_type`, `filer_type`,
  `asset_type_code` are nullable on `legislator_trades`. Sources write `None`
  on unknown rather than fabricating `'purchase'` / `'self'` / `'OT'` defaults.
- **Daily + weekly orchestrator** registered with Task Scheduler. See the
  [operations runbook](pipeline.md).

Live row counts (2026-05-03, post-Senate-eFD-historical-backfill):

| Schema.table / view | Rows | Notes |
|---|---:|---|
| `alt_political_us.legislator_trades` (raw) | 70,992 | Multi-source append-only: 53,081 House Clerk + 12,685 SSW historical + 5,162 Senate eFD HTML + 64 paper-LLM |
| `alt_political_us.legislator_trades_dedup` (view) | 69,728 | **Use this for analytics.** Source-deduped — see [Cross-source dedup view](#cross-source-dedup-view-2026-05-03) below. |
| `alt_political_us.legislators` | 12,766 | current 536 + historical ~12,230 |
| `alt_political_us.legislator_terms` | 45,530 | every term, every member |
| `alt_political_us.legislator_aliases` | 1 | Van Taylor seed; grows via curation |
| `alt_political_us.gov_contracts` | 4,914 | usaspending_direct + finnhub_usa_spending |
| `alt_political_us.bills` | 15,433 | Congress.gov backfill |
| `audit.raw_archive` | 19,375 | unified across all political sources |

For day-to-day operations (running the daily/weekly cycle, triaging anomalies,
adding overrides, adding new sources), see
[`pipeline.md`](pipeline.md).

---

## Cross-source dedup view (2026-05-03)

> Migration: [028_legislator_trades_dedup_view.py](../../migrations/versions/028_legislator_trades_dedup_view.py) · View: `alt_political_us.legislator_trades_dedup`

### Why it exists

Multiple endpoints land in `legislator_trades` under different `endpoint_id`
values, so the table-level UNIQUE key cannot collapse logical duplicates
across sources. In particular:

- **Senate Stock Watcher historical** (`senate_stock_watcher_historical`)
  covers **2012-01-01 through 2020-12-02** (12,685 rows, 6,217 with ticker).
- **Senate eFD direct scrape** (`senate_efd_ptr`) covers **2017-12-21 through
  today** (5,162 rows, 3,858 with ticker), with most volume from 2019 forward.

Where the windows overlap (filings 2019-2020), every ticker'd row in eFD has a
near-mirror row in SSW. They sit side-by-side in the table with different
`endpoint_id`. Without dedup, `SUM`/`COUNT` over the overlap window inflates
by ~50% on the senate side. Same problem at the per-source level: a single
PTR can be amended (republished under a new `filing_id`), and the amended
copy lands beside the original.

The view solves both at once: cross-source dedup *and* same-source
PTR-amendment dedup.

### Data sources contributing to `legislator_trades`

| Source | `data_endpoints.code` | Chamber | Coverage | Rows | Notes |
|---|---|---|---|---:|---|
| House Clerk PTR PDFs | `house_clerk_ptr` | house | 2014-today | 53,081 | The only house source — no cross-source overlap risk. PTR amendments handled by the dedup view. |
| Senate Stock Watcher (frozen 2021 mirror) | `senate_stock_watcher_historical` | senate | 2012 → 2020-12-02 | 12,685 | Third-party historical mirror. Ingested once. Useful for backfill of pre-eFD years. |
| Senate eFD HTML PTRs | `senate_efd_ptr` | senate | 2019-today (backfilled), 2017+ trades disclosed late | 5,162 | Direct from `efdsearch.senate.gov` via Playwright (residential IP required). Authoritative source. |
| Senate eFD paper PTRs (LLM-OCR'd) | `senate_efd_paper_llm` | senate | sparse 2021-2022 | 64 | Paper-filed PTRs that were OCR'd. Disjoint from `senate_efd_ptr` by construction. |

### Source precedence (when the same logical trade appears in multiple sources)

1. `senate_efd_ptr` — direct from source.gov, authoritative
2. `senate_efd_paper_llm` — same source, OCR risk
3. `senate_stock_watcher_historical` — third-party mirror
9. anything else — currently just `house_clerk_ptr` (no overlap, no contention)

### Partition key (the "trade fingerprint")

```
(bioguide_id, transaction_date, UPPER(ticker), transaction_type,
 filer_type, amount_min, amount_max)
```

The view applies a `ROW_NUMBER() OVER (PARTITION BY <fingerprint> ORDER BY
<precedence>, ...)` and keeps `rk = 1`. Tie-breakers within the same source:
prefer rows with `amount_min` populated, then with `filing_url`, then most
recent `as_of_time`.

**Rows where any of `bioguide_id`, `ticker`, or `transaction_type` is NULL
pass through unchanged.** They cannot be safely matched across sources.
This is critical for paper-PTR placeholders (no ticker, no transaction type),
unresolved-bioguide rows, and "complex assets" (e.g., partnership interests
without a ticker symbol).

`filer_type` and `amount_{min,max}` are **part of the fingerprint** so that
legitimate multi-trade days are not collapsed:

- A representative buying $1,001-$15,000 of DIS *and* $15,001-$50,000 of DIS
  on the same day = two real trades (different `amount_min`).
- A senator's `self` and `spouse` accounts both trading AAPL same day = two
  real trades (different `filer_type`).

### What gets deduped (current state, 2026-05-03)

| Bucket | Raw rows | Dedup rows | Collapsed |
|---|---:|---:|---:|
| `house_clerk_ptr` | 53,081 | 52,449 | 632 (PTR amendments + asset_name_raw / amount_str format variants of the same trade) |
| `senate_stock_watcher_historical` | 12,685 | 12,336 | 349 (mostly within the 2019-2020 boundary where eFD is the higher-precedence winner) |
| `senate_efd_ptr` | 5,162 | 4,879 | 283 (cross-source dupes against SSW) |
| `senate_efd_paper_llm` | 64 | 64 | 0 (disjoint by construction) |
| **TOTAL** | **70,992** | **69,728** | **1,264** |

### Same-source dedup (PTR amendments)

When a representative amends a PTR, the amended copy lands as a separate
row under a new `filing_id`, often with subtly reformatted `asset_name_raw`
or `amount_str`. The dedup view treats these as one trade. Of the 632
collapsed house rows:

- 451 groups had different `filing_id` (true PTR amendments)
- 391 groups had different `asset_name_raw` (e.g., "Apple Inc." vs "Apple Inc")
- 85 groups had different `asset_type_code` (`ST` vs `OT` classification flip)
- 66 groups had different `amount_str` only (formatting whitespace)

### Cross-source dedup (SSW ↔ eFD overlap, 2019-2020)

For 2019 filings: of 167 eFD rows that had bioguide+ticker, 166 (99.4%)
matched a SSW row exactly on the strict 5-key (bioguide+date+ticker+type+amount_str).
Of those, eFD won every dedup decision (it has higher precedence). Same
pattern for 2020.

### How to query

**Always use the view for analytics:**

```sql
-- Count trades by source — uses the view, sees one row per trade
SELECT source_code, COUNT(*) FROM alt_political_us.legislator_trades_dedup
GROUP BY 1;

-- Conjunction query (committee-relevant trade × govt contract)
-- The original example query in this doc would use ..._dedup, not the raw table.
```

**Use the raw table only for:**

- Audit trail / provenance (which sources reported a given trade)
- Re-ingestion idempotency checks
- Migration / schema work

The view is a regular (non-materialized) view — no `REFRESH` needed; reads
are cheap given table size.

### Senate eFD historical backfill — year-by-year progress (2026-05-03)

Filing-year breakdown (`senate_efd_ptr` only):

| Filing year | HTML | Paper placeholders | Total |
|---|---:|---:|---:|
| 2019 | 305 | 23 | 328 |
| 2020 | 291 | 23 | 314 |
| 2021 | 638 | 40 | 678 |
| 2022 | 711 | 27 | 738 |
| 2023 | 1,131 | 25 | 1,156 |
| 2024 | 917 | 20 | 937 |
| 2025 | 634 | 32 | 666 |
| 2026 | 341 | 4 | 345 |
| **Total** | **4,968** | **194** | **5,162** |

Trade-date can lead filing-year by up to ~24 months because PTRs disclose
trades the legislator made before filing (a few late-disclosure outliers go
back further). Earliest tx date in `senate_efd_ptr` is 2017-12-21.

Paper-placeholder rows are stand-ins for PTRs that were filed on paper
(scanned PDFs) where the OCR has not yet been run. They carry NULL ticker
and NULL transaction_type, so the dedup view passes them through unchanged.
The `senate_efd_paper_llm` endpoint is where OCR'd paper-PTR trades land
once the LLM extraction runs — those rows are tagged separately so the
provenance stays clear.

Backfill years still to run: **2014, 2015, 2016, 2017, 2018**. SSW historical
already covers those years; running eFD will produce more cross-source
dupes (the dedup view handles them, but it adds rows + bytes for redundant
coverage). Decision deferred — likely worth running for the placeholder
records of paper PTRs and the direct-source provenance, but not the highest
priority.

## Purpose

US legislators are required to file equity transactions under the **STOCK Act of 2012** via Periodic Transaction Reports (PTRs). The alpha thesis is not in following large-cap trades (AAPL, NVDA — crowded, no edge) but in **small/micro-cap names traded by members on relevant oversight committees**, cross-referenced with government contracts and lobbying data.

The binding constraint: filings have a **45-day reporting window**, so by the time data is public, the transaction is 1–45 days old. The `filing_date` IS the signal date. But the *structural backing* signal (member keeps buying, company keeps winning contracts) is durable across 90–180 days post-disclosure.

---

## Production ingestion — `src/factorlab/sources/political/` (LIVE 2026-04-30)

The current target is a denormalized ClickHouse redesign. See
[the ClickHouse architecture](../../architecture/02-database-clickhouse.md) for
the target model. The Postgres `alt_political_us.*` layout described below is
the legacy normalized implementation retained during migration.

The 8 source modules now have production ingestion code that writes directly into `alt_political_us`. Foundation utilities + 8 fully-implemented sources (legislators, house_clerk, senate_efd, senate_stock_watcher, lda, usaspending, finnhub_contracts, fec, congress_gov).

### Module layout

```
src/factorlab/sources/political/
├── _client.py                 # HTTP base: retry/backoff, raw_archive write, disk cache
├── _db.py                     # fast_upsert: auto-picks ORM / batched / COPY based on row count
├── _resolver.py               # name → SEC ticker (5-tier match, 11/11 self-test pass)
├── _state.py                  # checkpoint/resume per-source for long backfills
├── legislators/               # → 5 dim tables (legislators, terms, fec_ids, committees, assignments)
├── house_clerk/               # year-ZIPs → 8,150 PTRs → PDF parse → legislator_trades
├── senate_stock_watcher/      # 8,350 historical 2014-2019 → legislator_trades (one-shot)
├── senate_efd/                # Playwright (residential IP) → HTML PTRs → legislator_trades
├── lda/                       # paginated /filings/ → 4 tables (filings + activities + targets + lobbyists)
├── usaspending/               # POST /spending_by_award/ → gov_contracts (sovereign primary)
├── finnhub_contracts/         # /stock/usa-spending → gov_contracts (third-party parallel)
├── fec/                       # STUB (pending FEC_API_KEY)
├── congress_gov/              # STUB (pending CONGRESS_API_KEY)
└── orchestrator.py            # Phase 1→4 driver
```

### Drivers

```
scripts/us/political/us_political_backfill.py  # one-shot full historical backfill (interactive)
scripts/us/political/us_political_daily.py     # daily incremental (Task Scheduler hook)
```

### Backfill commands

```bash
# Phase 1 — reference dims (~5 min, runs anywhere)
python scripts/us/political/us_political_backfill.py --phase 1

# Phase 2 — trade events (~1.5h)
python scripts/us/political/us_political_backfill.py --phase 2 \
    --house-clerk-years 2014-2026 \
    --senate-efd-from 2020-01-01 --senate-efd-to 2026-04-30

# Phase 3 — conjunction (~25h, can chunk)
python scripts/us/political/us_political_backfill.py --phase 3 \
    --contract-fy-range 2014-2026 \
    --lda-years 2014-2026

# Phase 4 — verification (instant)
python scripts/us/political/us_political_backfill.py --phase 4

# All four
python scripts/us/political/us_political_backfill.py --all
```

### Runbook — FEC + Congress.gov backfill (verified 2026-05-01)

#### Pre-flight

```bash
# 1. Verify keys (no values printed)
python -c "import os; from dotenv import load_dotenv, find_dotenv; load_dotenv(find_dotenv(usecwd=True)); \
  print('FEC ok=', bool(os.getenv('FEC_API_KEY')) and os.getenv('FEC_API_KEY').isalnum() and len(os.getenv('FEC_API_KEY'))==40); \
  print('CG  ok=', bool(os.getenv('CONGRESS_API_KEY')) and os.getenv('CONGRESS_API_KEY').isalnum() and len(os.getenv('CONGRESS_API_KEY'))==40)"

# 2. Verify free disk space (need ~10 GB total for raw cache)
python -c "import shutil; t,u,f=shutil.disk_usage('.'); print(f'free={f/1e9:.1f}GB')"

# 3. Verify schema migrations are caught up
alembic current     # should show 027 or later

# 4. Status snapshot — baseline before backfill
python scripts/us/political/us_political_status.py
```

#### Run — sequential (recommended) or parallel

```bash
# OPTION A — single end-to-end run (preferred for first backfill)
# FEC committees + Mode A (590 corp PACs × 7 cycles) + Mode B (1700 PCCs × 5 cycles)
# + Congress.gov 117/118/119 list + detail + deep + hearings.
python scripts/us/political/us_political_backfill.py --phase 3
# Total wall time: ~12-16h depending on Mode B volume + cache state. Resumable.

# OPTION B — split FEC and Congress.gov for parallel terminals
# Different API keys, different state files, different rate-limit budgets.
python -m factorlab.sources.political.fec.ingest --committees --mode-a --mode-b &
python -m factorlab.sources.political.congress_gov.ingest --congress 117 --congress 118 --congress 119 &
```

#### Mid-run monitoring

```bash
# Quick status (no DB locks; safe to run while backfill is in flight)
python scripts/us/political/us_political_status.py
```

Reports: row counts per table, audit activity per endpoint (last 24h), state-checkpoint progress (PAC-cycles done, bills detailed/deep-fetched), raw cache footprint per source.

#### Recovery — common failure modes

| Symptom | Cause | Action |
|---|---|---|
| 429 + `Retry-After: <large>` for FEC | Per-minute burst exceeded 60/min | Throttle at 1.05s already enforced; if persistent, the API umbrella may have flagged the key. Wait the Retry-After window. |
| FK violation on `campaign_donations.candidate_id` | New committee-shaped ID FEC returned that wasn't in `legislator_fec_ids` | Already handled — `_to_donation_row(raw, valid_candidate_ids)` soft-NULLs. If error reappears, check `legislator_fec_ids` is populated. |
| `connection aborted` mid-pull | Transient FEC/Congress.gov outage | Retry/backoff in `PoliticalHTTPClient` handles 3 attempts. If exhausted, the loop's `try/except continue` skips that PAC-cycle and moves on. State is NOT marked done; next run retries. |
| Process crash mid-run | Power, OOM, manual kill | Re-run the same command. State checkpoints flush every 25 PACs / batch-size rows; cached responses on disk. Resume picks up where it left off. |
| `bill_actions` duplicates after schema change | Migration changed `action_id` semantics | Re-run with deterministic `uuid5(NAMESPACE_BILL_ACTIONS, ...)` (already in place). Old random-UUID rows can be cleaned via `DELETE WHERE substring(action_id::text, 15, 1) = '4'`. |
| Resolver re-run after alias-file edits | New aliases added | `python -m factorlab.sources.political.fec.ingest --re-resolve` updates `sponsor_company_ticker` in-place. Zero API calls. |

#### Daily / weekly cadence (post-backfill)

```bash
# Daily — wired in scripts/us/political/us_political_daily.py
#   - legislators YAML refresh (~30s)
#   - house_clerk current year (~2 min)
#   - senate_efd last 7 days (Playwright local; ~10 min)
#   - lda current year (~5 min)
#   - congress_gov active-congress Pass A + B + hearings (~3 min)
python scripts/us/political/us_political_daily.py --mode daily

# Weekly — adds usaspending + finnhub + FEC current cycle + Congress.gov Pass C
python scripts/us/political/us_political_daily.py --mode weekly
```

Schedule via Task Scheduler (Windows) or cron (Linux): daily at 06:00 UTC (Mon-Sat); weekly Sun 17:30 IST (12:00 UTC).

### Live-DB row counts (2026-05-03, after Senate eFD 2019-2020 backfill)

| Table / view | Rows | Source breakdown |
|---|---:|---|
| `legislators` | 12,766 | current 536 + historical ~12,230 |
| `legislator_terms` | 45,530 | every term every member ever served |
| `legislator_fec_ids` | 1,713 | FEC candidate IDs |
| `committees` | 559 | current + historical incl. subcommittees |
| `committee_assignments` | 3,879 | 119th Congress |
| `legislator_trades` (raw) | 70,992 | house_clerk_ptr 53,081 + senate_stock_watcher_historical 12,685 + senate_efd_ptr 5,162 + senate_efd_paper_llm 64 |
| `legislator_trades_dedup` (view) | 69,728 | Source-deduped per [Cross-source dedup view](#cross-source-dedup-view-2026-05-03). Use for analytics. |
| `gov_contracts` | 1,933 | usaspending_direct (LMT) + finnhub_usa_spending (LMT+PLTR) |
| `lobbying_filings` | 25 | smoke sample, full LDA backfill not yet run |
| `lobbying_activities` | 46 | |
| `contract_aliases` | 1 | LMT seed |
| `lobby_client_aliases` | 4 | from LDA smoke run |
| `raw_archive` | 47 | URL + metadata only; bytes on disk |

### What lands where (source → table mapping)

| Source | Module | Tables it writes |
|---|---|---|
| House Clerk PTRs | `house_clerk/` | `legislator_trades` (chamber='house', endpoint=`house_clerk_ptr`) + `raw_archive` |
| Senate eFD HTML PTRs | `senate_efd/` | `legislator_trades` (chamber='senate', endpoint=`senate_efd_ptr`) + `raw_archive` |
| Senate eFD paper scans (placeholder rows) | `senate_efd/` | `legislator_trades` (NULL ticker, asset_name_raw='[PAPER PTR — N pages — OCR pending]') + `raw_archive` |
| Senate eFD paper scans (post-OCR) | `senate_efd/` (LLM pass) | `legislator_trades` (chamber='senate', endpoint=`senate_efd_paper_llm`) |
| Senate Stock Watcher 2012-2020 | `senate_stock_watcher/` | `legislator_trades` (endpoint=`senate_stock_watcher_historical`). Frozen 2021 mirror — covers the pre-eFD-direct era. |
| unitedstates/congress-legislators | `legislators/` | `legislators` + `legislator_terms` + `legislator_fec_ids` + `committees` + `committee_assignments` |
| LDA `/filings/` | `lda/` | `lobbying_filings` + `lobbying_activities` + `lobbying_activity_targets` + `lobbying_activity_lobbyists` + `lobby_client_aliases` |
| USASpending direct | `usaspending/` | `gov_contracts` (source='usaspending_direct') + `contract_aliases` |
| Finnhub `/stock/usa-spending` | `finnhub_contracts/` | `gov_contracts` (source='finnhub_usa_spending') |
| FEC `/committees/` + `/schedule_a/` | `fec/` | `fec_committees` + `campaign_donations` (Mode A: corp-PAC outflows; Mode B: ≥$1K individuals to PCCs) |
| Congress.gov `/bill/` + `/hearing/` | `congress_gov/` | `bills` + `bill_sponsors` + `bill_committees` + `bill_actions` + `hearings` (3-pass bills + hearings; `hearing_witnesses` deferred) |

### Daily incremental

After backfill validates, wire `scripts/us/political/us_political_daily.py` into Task Scheduler:
- 06:00 UTC daily
- Refreshes legislators YAMLs (~30 sec)
- Pulls current-year House Clerk PTRs (~2 min)
- Senate eFD last 7 days via Playwright (~10-15 min, must be local)
- LDA current year (~5-10 min)
- Contracts toggled via `--skip-contracts` (typically weekly cadence, not daily)

### Storage split

- **Postgres** holds events + dimensions + URLs + metadata (~10 GB target)
- **Filesystem** under `data/political/raw/{source}/...` (gitignored) holds the fetched bytes — PDFs, HTMLs, JSONs, GIFs (~10 GB target)
- Total system footprint ~20 GB at MAX historical-mode

---

## Persistent storage — `alt_political_us` schema (LIVE 2026-05-01)

Schema lives in Postgres `alt_political_us.*` (26 tables). See [docs/architecture/database.md](../../architecture/database.md) for full ER + index map. Migrations: [007_create_alt_political_tables.py](../../migrations/versions/007_create_alt_political_tables.py) + [008_seed_alt_political_reference.py](../../migrations/versions/008_seed_alt_political_reference.py).

Every source documented in this file maps to a specific table:

| Source | Lands in | Resolver |
|---|---|---|
| House Clerk PTR PDFs | `legislator_trades` (chamber='house', source='house_clerk_ptr') | filer-name → `legislators.bioguide_id` |
| Senate eFD HTML PTRs | `legislator_trades` (chamber='senate', source='senate_efd_ptr') | filer-name → `legislators.bioguide_id` |
| Senate eFD paper-filed scans | `raw_archive` only (OCR deferred) | — |
| Senate Stock Watcher 2014-2019 historical | `legislator_trades` (source='senate_stock_watcher_historical') | senator_full → bioguide via `legislators-historical.yaml` |
| `unitedstates/congress-legislators` legislators-current.yaml | `legislators` + `legislator_terms` | direct |
| `unitedstates/congress-legislators` legislators-historical.yaml | `legislators` (in_office=false) + `legislator_terms` | direct |
| `unitedstates/congress-legislators` committees-current.yaml | `committees` (current rows) | direct |
| `unitedstates/congress-legislators` committees-historical.yaml | `committees` (is_current=false) | direct |
| `unitedstates/congress-legislators` committee-membership-current.yaml | `committee_assignments` | direct |
| House Clerk asset-type-codes (48) | `asset_type_codes` (seeded) | — |
| LDA `/api/v1/filings/` | `lobbying_filings` + `lobbying_activities` + `lobbying_activity_targets` + `lobbying_activity_lobbyists` | `client_name` → `lobby_client_aliases.ticker` |
| LDA `/constants/lobbyingactivityissues/` (79) | `lda_issue_codes` (seeded) | — |
| LDA `/constants/governmententities/` (257) | `lda_government_entities` (seeded) | — |
| Finnhub `/stock/usa-spending` | `gov_contracts` (source='finnhub_usa_spending') | already ticker-keyed |
| USASpending `/spending_by_award/` | `gov_contracts` (source='usaspending_direct') | `recipient_legal_name` → `contract_aliases.ticker` via SEC ticker resolver |
| FEC `/schedules/schedule_a/` | `campaign_donations` | `committee_id` → `fec_committees`; `candidate_id` → `legislator_fec_ids` |
| FEC `/committees/` | `fec_committees` | `sponsor_company_ticker` resolved via name match |
| Congress.gov `/bill/` | `bills` (one row per bill_uid) + `bill_actions` | direct |
| Congress.gov `/bill/.../cosponsors/` | `bill_sponsors` (role='sponsor'/'cosponsor') | direct |
| Congress.gov `/bill/.../committees/` | `bill_committees` (per-activity rows) | direct |
| Congress.gov `/hearing/` | `hearings` + `hearing_witnesses` | direct |
| All sources (raw response bytes) | `raw_archive` | — |
| Manual seed (docs Part 5) | `committee_sector_map` (21 rows seeded) | — |

**Country tagging**: every dim and event row carries `country_code` FK to `ref.countries.code`, defaulting to `'US'`. Multi-jurisdiction expansion (UK MP register, EU MEPs, India parliamentary disclosures) reuses the same tables — different `country_code`, same shape. See [docs/architecture/database.md](../../architecture/database.md) for the country-tagging convention.

**The two stable join keys**:
- `bioguide_id` — every legislator-side join. Nullable on events until name-resolution succeeds; ingestion never blocks on resolution failure.
- `ticker` — every company-side join. Denormalized into events (`legislator_trades.ticker`, `gov_contracts.ticker`, `lobbying_filings.client_ticker`) for fast queries; `security_id` FK to `ref.instruments.id` is the ground truth.

**The conjunction query** that motivates the schema:

```sql
-- Trades by relevant-committee members in tickers with active govt contracts AND lobbying
SELECT t.transaction_date, t.filing_date, l.last_name, l.first_name,
       t.ticker, t.transaction_type, t.amount_str,
       csm.signal_strength, csm.rationale,
       gc.awarding_agency, gc.action_date, gc.total_value,
       lf.client_name, lf.income, lf.expenses
FROM alt_political_us.legislator_trades_dedup t   -- ← view, not raw table
JOIN alt_political_us.legislators l USING (bioguide_id)
JOIN alt_political_us.committee_assignments ca USING (bioguide_id)
JOIN alt_political_us.committee_sector_map csm
  ON ca.country_code = csm.country_code AND ca.committee_id = csm.committee_id
LEFT JOIN alt_political_us.gov_contracts gc
  ON gc.ticker = t.ticker
  AND gc.action_date BETWEEN t.transaction_date - INTERVAL '90 days'
                         AND t.transaction_date + INTERVAL '90 days'
LEFT JOIN alt_political_us.lobbying_filings lf
  ON lf.client_ticker = t.ticker
  AND lf.filing_year = EXTRACT(YEAR FROM t.transaction_date)
WHERE t.transaction_type = 'purchase'
  AND csm.signal_strength IN ('extreme','very_high','high')
  AND ca.valid_to IS NULL  -- currently serving on the committee
ORDER BY t.filing_date DESC;
```

> **Note**: factor / signal queries should always pull from
> `legislator_trades_dedup` (the view), not the raw `legislator_trades` table.
> See [Cross-source dedup view](#cross-source-dedup-view-2026-05-03) for why.

---

## Verified status (2026-04-29)

This section is the empirically-verified ground truth as of today's probes. Where it conflicts with later sections, **this section is correct** — the rest of the doc preserves design context but parts are stale until rewritten.

### Live sources we can use, $0

| Source | Status | Coverage | Notes |
|---|---|---|---|
| **House Clerk per-PTR PDFs** | ✅ working | 100% House, 2014→today | Annual `{YEAR}FD.zip` is filing-index only; per-PTR PDFs at `disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf` are text-extractable with `pdfplumber` |
| **Senate eFD PDFs** (`efdsearch.senate.gov`) | 🟡 to build | 100% Senate, 2012→today | Cookie-session + agreement page; 1–2 days to build robust scraper. **The only free path to live Senate trades.** |
| **`unitedstates/congress-legislators` YAML** | ✅ working | 536 current members, 3,879 cmte assignments | Active, all bioguide-keyed |
| **Finnhub `/stock/usa-spending`** | ✅ working (free key) | Ticker-mapped govt contracts, 60/min, no daily cap | Returns parent corp, agency, NAICS, action_date, etc. — superior to direct USASpending for our use case |
| **USASpending.gov direct API** | ✅ working | All federal contracts back to 1984 | Recipient-name search; backup for Finnhub |
| **Senate LDA bulk XML** | ✅ available | All federal lobbying, quarterly | Free; client-name → ticker mapping is your problem |
| **SEC EDGAR / Finnhub `/stock/insider-transactions`** | ✅ available | SEC Form 4 (corporate insiders, NOT politicians) | Free; orthogonal cross-signal |

### Sources documented as live but actually dead

| Source | Status | Verified |
|---|---|---|
| Senate Stock Watcher S3 (`senate-stock-watcher-data.s3-*.amazonaws.com`) | ❌ HTTP 403 across all regions/paths | 2026-04-29 |
| `senatestockwatcher.com` | ❌ DNS does not resolve | 2026-04-29 |
| Senate Stock Watcher GitHub mirror | 🟡 frozen — last commit 2021-03-16, **data ends 2019-12-31** | 2026-04-29 |
| House Stock Watcher S3 (`house-stock-watcher-data.s3-*.amazonaws.com`) | ❌ HTTP 403 | 2026-04-29 |
| House Stock Watcher GitHub repo | ❌ HTTP 404 (repo removed) | 2026-04-29 |
| Capitol Trades BFF (`bff.capitoltrades.com`) | ❌ Cloudflare 503, ToS prohibits scraping | 2026-04-29 |
| House Clerk annual `{YEAR}FD.xml` for trades | ❌ Index only — has no transaction data | 2026-04-29 |

### Free tiers that are paid in practice

| Endpoint | Status |
|---|---|
| Finnhub `/stock/congressional-trading` | 403, paid only |
| Finnhub `/stock/lobby` | HTML paywall response, paid only |
| FMP senate/house-trading | 250 req/day cap — too tight for batch |
| Quiver Quantitative free tier | ~50 req/day — too tight for batch |
| OpenSecrets API | Discontinued 2025-04 (bulk CSV still free) |

### Falsified claims in this doc (preserved-but-wrong sections below)

1. **§1A "House Clerk XML has Ticker, AssetName, TransactionDate, etc."** — Wrong. The XML is filing-index only (DocID, Last, First, FilingType, FilingDate, StateDst). Transaction data lives in per-PTR PDFs. The annual ZIP also no longer contains PDFs.
2. **§1B "Senate Stock Watcher JSON as v1 shortcut"** — Wrong. SSW is dead. Direct eFD PDF scraping is the only free live Senate path.
3. **§2A "Senate Stock Watcher: free, daily updates"** — Wrong. Frozen 2019. Use only as 2014-2019 historical backfill.
4. **§2D Finnhub "/stock/congressional-trading + /stock/lobby + /stock/usa-spending all free 60/min"** — Partially wrong. Only `/stock/usa-spending` is free. Congressional-trading and lobby are paid.
5. **§6 Phase 1 "Daily incremental from Senate Stock Watcher"** — Wrong. SSW is dead; the live incremental path is direct PTR/eFD PDF parsing.

### Empirically observed quality

- **Senate Stock Watcher historical (2014-2019, 8,350 rows)**: 25.3% unresolved tickers (matches doc), missing fields `disclosure_date` and `senator_id` (doc claims they exist).
- **House Clerk PTR PDFs (sample of 30 most-recent 2024)**: 18 trades extracted from 11 PTRs = ~37% per-PTR hit rate with naive regex. ~80% achievable with parser tuning + table-aware extraction. Many "0-trade" PTRs are legitimate (no-trade filings from older formats).
- **Finnhub `/stock/usa-spending`**: LMT=932 contracts, RTX=2000 (capped), PLTR=434 — full schema with `recipientParentName`, `actionDate`, `awardingAgencyName`, `naicsCode`. Pre-mapped to ticker.
- **congress-legislators YAML**: 536 members (100 Sen + 436 Rep), 49 committees, 3,879 cmte assignments. Subcommittee keys are 6-char (e.g., `SSAS01`); first 4 chars map back to full committee.

---

## Part 1 — Raw Official Sources

### 1A. House Clerk (PRIMARY, free, build first)

| Field | Detail |
|-------|--------|
| URL | https://disclosures-clerk.house.gov/FinancialDisclosure |
| Auth | None |
| Volume | 435 representatives → ~4x Senate trade volume |
| Why first | No agreement page, no auth, PDFs are text-extractable |

**Two endpoints, two roles:**

```
# 1) Filing INDEX (annual ZIP of XML — discovery, no transaction data)
https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}FD.ZIP

# 2) Per-PTR PDF (the actual transactions — text-extractable, ~70KB each)
https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf
```

**⚠ Falsified claim above this section's earlier draft:** The annual `{YEAR}FD.xml` does NOT contain transaction-level fields. Verified 2026-04-29 against `2024FD.xml` (2,233 filings, 451 PTRs):

- ZIP contains only `{YEAR}FD.xml` + `{YEAR}FD.txt` — **no per-PTR PDFs inside**
- XML has 9 fields, all filing-level: `Prefix, Last, First, Suffix, FilingType, StateDst, Year, FilingDate, DocID`
- No nested transaction children. No tickers, no amounts.

**Real flow:** parse XML for `(DocID, Year, FilingType='P', filer name, FilingDate)` tuples → fetch each per-DocID PDF → parse PDF for transactions.

**FilingType codes (verified):** `C` original FD, `X` amended, `P` PTR, `O` blind trust / extension, `A` annual, `D` dispatch, `W` withdrawal, `H` hold, `T` trust, `B`, `G`, `E`. Filter to `P` for STOCK Act trades.

**PDF schema (verified by parsing 30 PTRs):**

Each transaction line in the PDF text has the shape:
```
[OwnerCode] AssetName (TICKER) [AssetTypeCode] TxType TxDate NotifDate $Min - $Max
                                                                           ↳ amount may wrap to next line
```
- `OwnerCode`: `SP` (spouse), `JT` (joint), `DC` (dependent child), `JR` (junior); absent = self
- `AssetTypeCode`: `[ST]` stock, `[GS]` govt security, `[CS]` corporate, `[OP]` option, `[ET]` ETF, `[OT]` other, etc.
- `TxType`: `P` purchase, `S` sale, `E` exchange (with optional `(partial)` flag)
- Dates: `MM/DD/YYYY`
- Amount: STOCK Act bucket strings like `$1,001 - $15,000` (may wrap onto next line in PDF)

**Gotchas (verified):**
- No RSS/webhook — near-real-time requires polling the ASPX search interface
- Annual ZIP regenerates lazily — use for backfill, poll search for incremental
- ~37% naive-regex parse hit rate per PTR; ~80% achievable with table-aware extraction + multi-format heuristics. Many "0-trade" PTRs are legitimate (no transactions in period)
- Some filer names in XML have honorifics inserted as middle name (`"Marjorie Taylor Mrs Greene"`) — fuzzy match needed against legislators-current
- Amendments lack parent `DocID` — linkage requires probabilistic matching on `(name, date, asset, type, amount)`

**House Clerk PTR historical load — tiered pipeline (completed 2026-05-01)**

8,155 PTRs across 2014–2026 processed via `scripts/us/political/house_clerk/us_political_house_clerk_historical.py` (multiprocessing, 7 workers). Result: **53,081 rows** in `alt_political_us.legislator_trades`, 386 distinct legislators.

Two PDF format families exist:
- **Modern (2022+):** trailer delimiter `F S:` / `FILING STATUS:`, explicit `[XX]` asset-type codes, ~83% per-PTR extraction rate
- **Older (2014–2021):** `FILING STATUS:` only, no `[XX]` codes (all `asset_type_code = NULL`), case-mixed glyph output from pdfplumber (`SBuX` → normalized to `SBUX`)

**Four-tier quality gate:**

| Tier | Condition | Action | Count |
|------|-----------|--------|------:|
| 1 — auto-insert | Electronic + valid trades + bioguide resolved | Inserted directly | ~50,500 |
| 2 — review queue | Trades extracted but quality gate failed (bioguide unresolved, header pollution, no ticker) | Logged to `tier2_review.csv`; 17/20 rescued via `scripts/rescue_house_clerk_tier2.py` | 20 |
| 3 — paper placeholder | `len(cleaned) < 200 OR anchor_matches == 0` | Placeholder row inserted with bioguide | ~2,560 |
| 4 — Claude direct-read | Electronic text-rich but parser extracted 0 trades | Read visually via PDF tool; 1 real trade recovered (James French Hill, AAWW) | 1 |

**Strict-NULL policy:** `asset_type_code = NULL` is accepted for older-format PTRs. No fabricated `OT`/`ST` fallbacks. Bioguide resolution: NULL is emitted if the matcher can't achieve a confident first-name overlap — wrong beats no-entry.

**Bioguide resolution improvements (to `_bioguide.StrictBioguideMatcher`):** compound surnames (Hinson Arenholz), middle-name preferred names (C. Scott Franklin), nicknames (Cindy → Cynthia, 80+ pairs), apostrophe normalization (U+2019), diacritics (Barragán), comprehensive 3-stage split-search. Bioguide resolve rate: ~99.8%.

**Manual override:** Nicholas V. Taylor (TX-03) → `T000479` (bioguide stores first="Van"; verified via `2022_20020767.pdf`). Encoded in `scripts/rescue_house_clerk_tier2.py::MANUAL_BIOGUIDE_OVERRIDES`.

### 1B. Senate eFD (PRIMARY for Senate, free, LIVE 2026-04-30)

| Field | Detail |
|-------|--------|
| URL | https://efdsearch.senate.gov |
| Auth | No auth, but requires accepting an agreement page (Akamai-protected; residential IP required) |
| Format | HTML search → HTML PTRs (post-2018) + paper-scan GIFs (older / opt-out senators) |
| Volume | 100 senators |
| Difficulty | Playwright + residential IP. Cloud / VPS IPs are Akamai-blocked. |

**⚠ Earlier "v1 shortcut to Senate Stock Watcher" recommendation is dead** (see §2A). Direct eFD scraping is the only free path to live Senate trades. SSW remains useful for 2012-2018 historical backfill.

**Agreement page is the #1 scraping failure point.** Session cookie expires on inactivity. Scraper detects redirect back to `/search/home/` and re-POSTs the agreement form automatically.

**Module**: `src/factorlab/sources/political/senate_efd/` — 4 files (`scraper.py` Playwright driver, `parser.py` HTML extraction, `ingest.py` upsert pipeline, `__init__.py`).

**Filing types**: Periodic Transaction Reports (PTRs), Annual Financial Disclosures, Amendments. Filter to PTRs for trades.

**Filing format split (2026-05-03 backfilled state):**
- **HTML PTRs**: 4,968 rows in `legislator_trades`, all from `senate_efd_ptr` endpoint. Standard parse path.
- **Paper-scan GIFs**: 194 placeholder rows + 64 OCR'd rows. Some senators (notably Blumenthal, Boozman) file on paper; eFD serves these as multi-page GIF scans. Scraper downloads the GIF, writes a placeholder row to `legislator_trades` with `asset_name_raw='[PAPER PTR — N pages — OCR pending]'` and NULL ticker / NULL transaction_type so the dedup view passes them through unchanged. When LLM-OCR runs (separate pipeline, `senate_efd_paper_llm` endpoint), real trade rows get inserted alongside the placeholders.

**Backfill commands (one year at a time, recommended for headed Playwright runs):**

```bash
# Headed (visible browser) — required when Akamai serves a CAPTCHA challenge
python scripts/us/political/us_political_backfill.py --phase 2 \
  --senate-efd-from 2019-01-01 --senate-efd-to 2019-12-31 \
  --senate-efd-headed --senate-efd-max-filings 500 2>&1 | Tee-Object logs/senate_efd_2019.log

# Headless — works once a residential IP is warmed
python scripts/us/political/us_political_backfill.py --phase 2 \
  --senate-efd-from 2024-01-01 --senate-efd-to 2024-12-31
```

**Backfill progress (2026-05-03):**

| Filing year | HTML | Paper | Trades inserted (this run) | Total in DB (after dedup) |
|---|---:|---:|---:|---:|
| 2019 | 91 | 23 | 328 | 305 |
| 2020 | 91 | 23 | 314 | 291 |
| 2021 | 91 | 20 | 442 | 638 |
| 2022 | 80 | 18 | 595 | 711 |
| 2023 | 84 | 15 | 812 | 1,131 |
| 2024 | 105 | 15 | 620 | 917 |
| (2025, 2026 = continuous incremental, not single backfill batches) | | | | 666 + 341 |

Years 2014-2018 are still to backfill from eFD. SSW historical already
covers that window. The dedup view will collapse cross-source duplicates
when those years run; eFD wins precedence so the table grows in size but
analytics remain correct via the view.

**Critical gotcha: ingest is single-batch.** The eFD ingest builds the full
`pending` list in memory and `fast_upsert`s once at the very end of the
parse loop. If the run dies after `[senate_efd] downloaded N filings` but
before `[senate_efd] done: IngestResult(...)`, **zero rows reach the DB**.
The downloaded HTML/GIF files do persist to `data/political/raw/senate_efd/`,
so re-running is fast. See [`src/factorlab/sources/political/senate_efd/ingest.py:242-265`](../../src/factorlab/sources/political/senate_efd/ingest.py#L242-L265).

**Schema parity with House**: owner, ticker, asset_name_raw, asset_type, tx_type, tx_date, notif_date, amount_min/max/mid. All rows tagged `endpoint_id` for the senate_efd_ptr endpoint via `ref.data_endpoints`.

**Backfill split (current):**
- 2012-2018: Senate Stock Watcher GitHub mirror (12,685 rows, frozen) — see §2A
- 2019-today: Senate eFD direct (live, ongoing daily incremental)

### 1C. Congress.gov API (bills, hearings, members — verified 2026-05-01)

| Field | Detail |
|-------|--------|
| URL | https://api.congress.gov/v3/ |
| Auth | Free api.data.gov key (sign up at api.congress.gov/sign-up/, instant). Same key works for FEC. |
| Rate limit | **20,000 req/hour** verified empirically (`X-Ratelimit-Limit: 20000`). Docs claim 5K/hr — we observe higher. ~1.0s sleep between calls leaves comfortable headroom. |
| Format | JSON (`?format=json`) or XML |
| Total scope | 117th-119th Congresses: 52,576 bills, 5,115 hearings |

**What we ingest from this API**

| Endpoint | Lands in | Pass type |
|---|---|---|
| `GET /v3/bill/{c}` (list, paginated) | `bills` skeleton | A — list pull |
| `GET /v3/bill/{c}/{type}/{n}` (detail) | `bills` enrichment + primary `bill_sponsors` | B — detail enrichment |
| `GET /v3/bill/{c}/{type}/{n}/cosponsors` | `bill_sponsors` (role='cosponsor') | C — deep fetch (priority only) |
| `GET /v3/bill/{c}/{type}/{n}/committees` | `bill_committees` | C — deep fetch (priority only) |
| `GET /v3/bill/{c}/{type}/{n}/actions` | `bill_actions` | C — deep fetch (priority only) |
| `GET /v3/hearing/{c}` (list) | discover jacket numbers | one-shot |
| `GET /v3/hearing/{c}/{chamber}/{jacket}` | `hearings` | one-shot |

**Member metadata** is sourced from `unitedstates/congress-legislators` YAML, NOT this API — see §1A's legislators ingestion. Congress.gov member endpoints provide overlapping but less complete data (no historical terms, sparse cross-system IDs).

**⚠ CRITICAL — `policyArea` is NOT in list-mode.** The list endpoint returns only `{congress, type, number, title, originChamber, latestAction, updateDate, url}`. To filter bills by policy area we MUST detail-fetch every bill. This drives the 3-pass design.

**Three-pass bill ingestion**

```
Pass A — list pull         210 calls       3 congresses × ~70 pages of 250
Pass B — detail enrich   52,576 calls       every bill, get policyArea + primary sponsor
Pass C — deep fetch      ~63,000 calls       only priority-policy-area bills (3 sub-endpoints each)
                       ──────────
Total                   ~115,800 calls      ~6 hours at 20K/hr
```

Each pass writes to its own `_state.State` checkpoint and is independently resumable.

**Priority `policyArea` filter** (bills passing this set get Pass C deep-fetch — covers all market-moving legislation):

```
Armed Forces and National Security      Public Lands and Natural Resources
Health                                   Agriculture and Food
Finance and Financial Sector             Labor and Employment
Energy                                   Housing and Community Development
Science, Technology, Communications      Economics and Public Finance
Taxation                                 Commerce
Foreign Trade and International Finance  Environmental Protection
Transportation and Public Works
```

Skipped (procedural / non-market): Government Operations, Congressional Operations, Civil Rights, Education, Families, Native Americans, Sports and Recreation, Social Welfare, Arts/Culture/Religion, Emergency Management.

**Hearings** — also two-pass (list → detail). 5,115 hearings × 1 detail call ≈ 15 min.

**Field mapping** (full mapping including sub-resources in `playground/explore/congress_gov/NOTES.md`):

| Schema column (`alt_political_us.bills`) | Source (detail) |
|---|---|
| `bill_uid` | `f'US-{congress}-{type}-{number}'` (constructed) |
| `congress` / `bill_type` / `bill_number` | direct |
| `policy_area` | `policyArea.name` (detail-only) |
| `introduced_date` | `introducedDate` (detail-only) |
| `latest_action_date` | `latestAction.actionDate` |
| `latest_action_text` | `latestAction.text` |
| `update_date` | `updateDate` |

| Schema column (`alt_political_us.bill_committees`) | Source |
|---|---|
| `committee_id` | `committees[].systemCode.upper()` ⚠ |

| Schema column (`alt_political_us.hearings`) | Source (detail) |
|---|---|
| `jacket_number` | `jacketNumber` |
| `title` / `citation` / `chamber` / `congress` | direct |
| `date_held` | `dates[0].date` |
| `committee_id` | `committees[0].systemCode.upper()` ⚠ |

**Gotchas (verified 2026-05-01):**

1. **`policyArea` only in detail** — biggest cost driver; ~52K calls just to learn which bills to deep-fetch.
2. **`hearing_witnesses` table will be empty in v1.** Witness lists are not exposed as structured fields — they live in the formatted transcript PDF/HTML at `formats[].url`. Schema is preserved; population deferred until we add a transcript parser.
3. **`committees[].systemCode` is lowercase + `00`-suffixed for full committees** (`hssy00`, `hsif03`); our `committees.committee_id` is Thomas-style 4-char (`HSSY`, `HSIF`) with no suffix for full committees. The `_normalize_committee_id` helper strips trailing `00` and uppercases. For unknown committees (select committees, new committees not in our YAML snapshot), Pass C pre-loads the valid set and FK-filters writes to `bill_committees`; hearings soft-NULL the `committee_id`.
4. **Bill detail URL must use lowercase type**: `/bill/119/hr/1`, NOT `/bill/119/HR/1`.
5. **`bill_actions.action_id` uses deterministic `uuid5(NAMESPACE_BILL_ACTIONS, f"{bill_uid}|{action_date}|{action_text[:500]}")`**. Same input → same UUID → idempotent on re-run without a DB unique-constraint migration. Critical for resumable Pass C.
6. **Bills can have `policy_area = null`** — happens for very recent bills before Library of Congress tagging. Schema already nullable.
7. **`sponsors[].district` is null for senators.** Schema already nullable.
8. **`bill_sponsors` writes use `fast_upsert` (not `bulk_insert_ignore`)** so cosponsor `withdrawn_date` updates after first ingest. `bulk_insert_ignore` would silently lose withdrawals.
9. **Use `updateDateIncludingText` for incremental** — catches text revisions, not just metadata changes. `updateDate` alone misses re-introductions.
10. **HR 1 example** (verified 2026-05-01): 119th-congress reconciliation bill, `policyArea = "Economics and Public Finance"`, 1 sponsor, 0 cosponsors, 1 committee, 59 actions, 240 subjects, 5 summaries.

**Reference**: full probe results in `playground/explore/congress_gov/NOTES.md` (gitignored, mirrored here as system-of-record).

### 1D. Government Contracts — TWO PATHS, both verified

#### Path 1: Finnhub `/stock/usa-spending` (PRIMARY — ticker-mapped)

| Field | Detail |
|-------|--------|
| URL | https://finnhub.io/api/v1/stock/usa-spending |
| Auth | Free key (already in our `.env` as `FINNHUB_API_KEY`) |
| Rate limit | 60 req/min, no daily cap (verified) |
| Source | FPDS via Finnhub's recipient-name → ticker mapping layer |
| Why preferred | **Ticker-keyed**, parent-corp resolved (subsidiary roll-up done for us) |

**Request:**
```
GET /api/v1/stock/usa-spending?symbol=LMT&from=2024-01-01&to=2025-12-31&token=KEY
```

**Schema (verified per-row):**
```json
{
  "symbol": "LMT",
  "recipientName": "LOCKHEED MARTIN GLOBAL, INC.",   // the LEGAL entity that won
  "recipientParentName": "LOCKHEED MARTIN CORP",      // parent (matches ticker)
  "country": "USA",
  "totalValue": 113520.69,                            // contract ceiling
  "outlayedAmount": 0,                                // money already disbursed
  "obligatedAmount": 113520.69,                       // money obligated/committed
  "potentialAmount": 113520.69,                       // max if all options exercised
  "actionDate": "2025-12-11",                         // most recent modification
  "performanceStartDate": "2018-09-05",
  "performanceEndDate": "2018-12-31",
  "awardingAgencyName": "Department of Defense",
  "awardingSubAgencyName": "Department of the Navy",
  "awardingOfficeName": "COMMANDER",
  "performanceCountry": "EGY",                        // where work is done
  "performanceCity": "",
  "performanceState": "",
  "performanceCongressionalDistrict": "NY-22",        // ⭐ MAPS TO A SPECIFIC HOUSE MEMBER
  "awardDescription": "CETS SITE VISIT - YEAR 1",
  "naicsCode": "541330",                              // ⭐ FOR SECTOR JOIN
  "permalink": "https://www.usaspending.gov/award/CONT_AWD_M6785418F2011_.../",
  "lastModifiedDate": "2025-12-11"
}
```

**Verified scale (2024-2025 window):** LMT=932 contracts, RTX=2000 (cap), PLTR=434, NOC=2000.

**Field-level signal interpretation:**

| Field | What it tells you | Trade alpha |
|---|---|---|
| `actionDate` | Date of latest contract action (award OR modification) | **Senator buys 30 days BEFORE actionDate = pre-public information; AFTER = news-following** |
| `totalValue` − `outlayedAmount` | Future revenue runway not yet on the income statement | Stock-priced for past, not future. Big delta = visibility edge |
| `obligatedAmount` ↗ over time | Funding ramp pattern | Rising obligation = program winning, scoping up |
| `potentialAmount` − `totalValue` | Optional ceiling not yet exercised | Tail upside if all options taken |
| `performanceStartDate` (future) | Revenue not yet booked | Most actionable signal |
| `awardingAgencyName/SubAgency` | Which committee oversees this | **DOD → Senate Armed Services + Defense Approps** |
| `performanceCongressionalDistrict` | Which House district benefits | Cross-ref to House member (constituency interest, may also be self-trade) |
| `naicsCode` | 6-digit sector code | Join to GICS / committee jurisdiction map |
| `awardDescription` | Free-text deliverable | Mineable for tech keywords (e.g. "MAVEN" = AI program → PLTR signal) |

**Gotchas:**
1. **Subsidiary roll-up is partial** — `recipientParentName` is the parent if FInnhub's mapper found one, else the legal entity. Some parents missing.
2. **2000-result cap per call** — for big contractors (LMT, NOC, RTX), use narrower `from/to` windows and paginate by date.
3. **`actionDate` ≠ `awardDate`** — actionDate updates with every modification, so a contract from 2018 can show up in 2025 results because of a recent mod. To find new awards specifically, dedup by Award ID (visible in `permalink`).
4. **NAICS codes are 6-digit** — broader sector filters need to truncate to 4 or 2 digits.
5. **Empty `performanceState/City`** when work is overseas — `performanceCountry` is the indicator.

#### Path 2: USASpending.gov direct API (FALLBACK — recipient-name search)

| Field | Detail |
|-------|--------|
| URL | https://api.usaspending.gov |
| Auth | None (free, public) |
| Format | JSON REST, POST search |
| Source | FPDS (Federal Procurement Data System) — same upstream as Finnhub |

**Use this when:**
- Need full historical contracts back to 1984 (Finnhub may cap older)
- Need contracts for a non-public-equity company (no ticker yet)
- Cross-validate Finnhub mappings

**Verified working endpoint:**
```
POST /api/v2/search/spending_by_award/
body: {
  "filters": {
    "recipient_search_text": ["LOCKHEED MARTIN"],
    "award_type_codes": ["A","B","C","D"],   // contract types
    "time_period": [{"start_date":"2024-10-01","end_date":"2025-09-30"}]
  },
  "fields": ["Award ID","Recipient Name","Awarding Agency","Award Amount",
             "Description","Start Date","End Date"],
  "page": 1, "limit": 100, "sort": "Award Amount", "order": "desc"
}
```

**Falsified earlier doc claim:** field names like `Action Date` and `Period of Performance Start Date` were claimed but the search endpoint actually accepts `Start Date` / `End Date` / `Last Date to Order` (the verbose names are for the download endpoint, not search). Verified empirically with $35B Lockheed F-35 contract test.

### 1E. Senate Lobbying Disclosures (LDA — REST API, free, verified)

| Field | Detail |
|-------|--------|
| URL | https://lda.senate.gov/api/v1/ |
| Auth | None |
| Rate limit | None observed |
| Format | Pure JSON REST, paginated (`?page_size=N&page=M`) |
| Total scope | 1,945,808 filings since 1999, 134,618 unique clients, 79 issue codes, 257 govt-entity targets |

**Endpoints (probed 2026-04-29):**
```
GET /api/v1/                                              # API root - 9 endpoint families
GET /api/v1/filings/?client_name=...&filing_year=...      # WHAT WE WANT - quarterly lobbying reports
GET /api/v1/clients/                                      # company directory
GET /api/v1/registrants/                                  # lobby firms directory
GET /api/v1/lobbyists/                                    # individual lobbyist directory
GET /api/v1/contributions/                                # PAC + 501(c) contributions
GET /api/v1/constants/filing/lobbyingactivityissues/      # 79-code issue taxonomy
GET /api/v1/constants/filing/governmententities/          # 257 lobbyable agencies
GET /api/v1/constants/filing/filingtypes/                 # filing-type code list
```

**Filing types:** `RR` Registration · `MM` Mid-Year · `Q1`-`Q4` Quarterly Report · `RA`/`MA`/`Q*A` Amendments · `TR` Termination

**Filing schema (relevant fields):**
```json
{
  "filing_uuid": "00013780-...",
  "filing_type": "Q1",
  "filing_year": 2025,
  "filing_period": "first_quarter",
  "income": "20000.00",          // What the registrant was PAID by client (lobby firm income)
  "expenses": null,              // What client paid IN-HOUSE (alternative to income; mutually exclusive)
  "dt_posted": "2025-04-16T...",
  "filing_document_url": "https://lda.senate.gov/filings/public/filing/{uuid}/print/",

  "client": { "name": "PFIZER INC", "id": 188017, ... },
  "registrant": { "name": "ALTRIUS GROUP, LLC", "id": 40022172, ... },

  "lobbying_activities": [
    {
      "general_issue_code": "TRD",                          // 79-code taxonomy
      "general_issue_code_display": "Trade (domestic/foreign)",
      "description": "Medical Supply Chain Resiliency Act; ...",   // free text - bills, topics
      "lobbyists": [
        { "lobbyist": {"first_name": "WILLIAM", "last_name": "MORLEY"},
          "covered_position": "General Counsel US Senator Arlen Specter",   // revolving-door tag
          "new": false }
      ],
      "government_entities": [                              // who they lobbied
        {"id": 2, "name": "HOUSE OF REPRESENTATIVES"},
        {"id": 1, "name": "SENATE"}
      ]
    }
  ]
}
```

**Verified scale (2025 only) for known equity tickers:**

| Ticker | Filings | Income $ | Expenses $ | Top issues | Top targets |
|---|---:|---:|---:|---|---|
| **PFE** | 44 | $1.88M | $27.08M | TRD, HCR, TAX | HOUSE, SENATE, HHS |
| **LMT** | 54 | $1.81M | $19.44M | **DEF**, BUD, TAX | SENATE, HOUSE, **DOD** |
| **MSFT** | 74 | $3.29M | $9.36M | CPI, TEC, SCI | HOUSE, SENATE, EOP |
| **AAPL** | 47 | $3.32M | $17.96M | TRD, CPT, LBR | HOUSE, SENATE, EOP |
| **PLTR** | 39 | $3.35M | $6.05M | **DEF**, SCI, AVI | SENATE, HOUSE, **VA** |
| **META** | 93 | $3.78M | $32.06M | CPI, SCI, LAW | SENATE, HOUSE, **Treasury** |
| **RTX** | 26 | $1.08M | — | DEF, BUD, GOV | HOUSE, SENATE, **DOS** |
| **AMZN** | 12 | $715K | — | CPI, LBR, TRA | SENATE, HOUSE, USDA |
| **UNH** | **0** | — | — | — | (filed under different name — see gotcha) |
| **GOOGL** | **0** | — | — | — | (filed as "GOOGLE LLC" not "ALPHABET INC.") |

**Issue codes useful for committee mapping** (full list in `playground/data/lda/_codes_2025.json`):
- `DEF` Defense → Senate Armed Services / House Armed Services
- `HCR` Health Issues + `MMM` Medicare/Medicaid + `PHA` Pharmacy + `MED` Medical → Senate HELP / House Energy & Commerce
- `TAX` Taxation + `BUD` Budget/Approps → Senate Finance + Appropriations / House Ways & Means + Appropriations
- `BAN` Banking + `FIN` Financial Institutions + `INS` Insurance → Senate Banking / House Financial Services
- `ENG` Energy/Nuclear + `FUE` Fuel/Gas/Oil + `ENV` Environment → Senate Energy & Natural Resources + EPW
- `TEC` Telecommunications + `CPI` Computer Industry + `SCI` Science/Tech + `CPT` IP → Senate Commerce / House Energy & Commerce
- `INT` Intelligence → Senate SLIN / House HLIG
- `IMM` Immigration + `LAW` Law Enforcement → Senate Judiciary / House Judiciary
- `TRD` Trade + `FOR` Foreign Relations → Senate Finance + SFRC / House Ways & Means + HSFA

**Signal interpretation per filing:**
- `income > 0` → external lobby firm hired (registrant ≠ client). Income is fee paid TO the firm BY the client.
- `expenses > 0` → in-house lobbying. Expenses is the client's own internal lobbying cost.
- These are mutually exclusive per filing type.
- A SPIKE in quarterly spend (e.g. >2x prior 4-quarter avg) is the freshest "something is brewing" signal.
- `lobbying_activities[].government_entities` ⊃ a specific committee/agency = direct targeting.
- `lobbying_activities[].description` is mineable for bill numbers, regulatory references, NAICS keywords.
- `covered_position` field reveals revolving-door lobbyists (e.g., former Senate staff).

**Gotchas (verified):**
1. **Client-name search is exact-match-ish** — `client_name=ALPHABET INC.` returns 0; `client_name=GOOGLE LLC` works. Same for `UNITED HEALTH GROUP INC.` (0) vs `UnitedHealth Group Incorporated`. Need a name → ticker map with multiple aliases per ticker. Build it once from the full `clients/` endpoint by ticker-fuzzy-matching company-name strings.
2. **Income vs expenses asymmetry** — pre-2014 filings may have income/expense as null even when activity exists; rely on `lobbying_activities` count, not just dollar amount.
3. **Subsidiaries lobby separately** — `Microsoft Corporation` and `LinkedIn` and `GitHub Inc.` all roll up to MSFT. Need a parent-ticker map.
4. **Issue codes are filer-self-reported** — generally accurate, but free-text `description` is the truth source for bill-specific signals.
5. **Quarterly cadence** — the freshest filing tells you what they were lobbying on UP TO 30-90 days ago. Less timely than PTRs but more timely than quarterly earnings.
6. **Termination filings (`TR`)** — when a registrant stops representing a client. Useful negative signal.

### 1F. FEC OpenAPI (campaign finance — REST API, free, verified 2026-05-01)

| Field | Detail |
|-------|--------|
| URL | https://api.open.fec.gov/v1/ |
| Auth | Free api.data.gov key (sign up at api.open.fec.gov/developers, instant). Same key works for Congress.gov. |
| Rate limit | **60 req/min** per key (verified empirically — `X-Ratelimit-Limit: 60`). NOT the 7,200/hr or 1K/hr that older sources claim — it's a per-MINUTE bucket. ≥1.0s sleep between calls in production. |
| Format | JSON. Pagination is `page`+`per_page` for first ~10K results, then **cursor-based** (`last_indexes`) for deeper pulls. |
| Total scope | Schedule A: ~3.6M ≥$1K contributions/cycle nationwide; ~50M raw rows/cycle. Schedule B: similar order. |

**Endpoints we use:**
```
GET /v1/committees/?q=NAME                              # name lookup for corporate-PAC discovery
GET /v1/committees/?committee_type=Q&organization_type=C  # corp-PAC sweep (Qualified-PAC, Corporate-sponsored)
GET /v1/candidate/{cand_id}/committees/                 # resolve candidate → Principal Campaign Committee
GET /v1/schedules/schedule_a/?contributor_id=PAC_ID     # Mode A: PAC outflows to candidate committees
GET /v1/schedules/schedule_a/?committee_id=PCC&min_amount=1000  # Mode B: ≥$1K individuals to a candidate
```

**⚠ CRITICAL — `candidate_id` filter on `/schedule_a/` is silently dropped.** Verified 2026-05-01:

| Filter | `pagination.count` |
|---|---:|
| `two_year_transaction_period=2024 & min_amount=1000` (no cand) | 3,636,694 |
| `+ candidate_id=ZZZZ99999` (bogus)                              | 3,636,694 (filter dropped) |
| `+ candidate_id=H8CA05035` (Pelosi, real)                       | 3,636,694 (filter dropped) |
| `committee_id=C00213512` (Pelosi's PCC, no cand filter)         | **1,267 ← real** |

**Implication for Mode B**: cannot pull "donations to candidate X" with `candidate_id`. Must resolve candidate → **Principal Campaign Committee** first, then filter by `committee_id`.

**Candidate → PCC resolution** (the extra step Mode B needs):

```
GET /candidate/{cand_id}/committees/
→ filter results where designation == 'P'  (Principal Campaign Committee)
→ use that committee_id for /schedule_a/?committee_id=...
```

Designation codes: `P`=principal campaign, `J`=joint fundraiser, `U`=unauthorized PAC, `A`=authorized. Verified for Pelosi (`H8CA05035` → 4 committees, 1 with `P`).

**Cursor pagination** (deeper than ~10K results):

```python
# pagination.last_indexes from any response
{"last_contribution_receipt_date": "2024-11-04", "last_index": "4011520251130612027"}
# pass these as query params on the next request — NOT page=N
```

`page`+`offset` returns errors past 10,000 rows. Cursor is returned on every page; use it from page 1.

**Two ingestion modes (decided 2026-05-01):**

| Mode | What | Filter | Volume (empirical) |
|---|---|---|---:|
| **A — PAC → candidate** | Corporate-PAC outflows to all recipient committees | `/schedule_a/?contributor_id=PAC_ID&two_year_transaction_period=YYYY` | ~50 corp PACs × 1,000 rows × 6 cycles ≈ **300K rows** |
| **B — Individual ≥$1K → candidate** | Large individual donations to sitting members' PCCs | `/schedule_a/?committee_id=PCC&two_year_transaction_period=YYYY&min_amount=1000` | 1,713 candidates × ~500 rows × 4 cycles (2018-24) ≈ **3.4M rows** |

Per-PAC verified counts (cycle=2024): Lockheed (`C00303024`)=1,574, Microsoft (`C00227546`)=531, Pfizer (`C00016683`)=573, Raytheon (`C00035683`)=6 (low — name-lookup variance). Per-PCC verified: Pelosi (`C00213512`)=1,267 ≥$1K donations.

**Field mapping — Schedule A row → `alt_political_us.campaign_donations`** (verified against `playground/explore/fec/NOTES_payload_individual.json`):

| Schema column | Schedule A field | Notes |
|---|---|---|
| `sub_id` | `sub_id` | Unique-constraint key |
| `cycle` | `two_year_transaction_period` | int |
| `donor_name` | `contributor_name` | |
| `donor_employer` | `contributor_employer` | individual-donor signal source |
| `donor_occupation` | `contributor_occupation` | |
| `donor_state` | `contributor_state` | |
| `donor_zip` | `contributor_zip` | trim to ≤10 |
| `donor_city` | `contributor_city` | |
| `donor_committee_id` | `contributor_id` | non-null when `entity_type='COM'` (Mode A) |
| `amount` | `contribution_receipt_amount` | float → Numeric(15,2) |
| `date` | `contribution_receipt_date` | ISO date |
| `transaction_type` | `receipt_type` | code (e.g. `"15"`, `"17"`) |
| `recipient_committee_id` | `committee_id` | filer's committee |
| `recipient_committee_name` | `committee.name` | nested dict |
| `candidate_id` | `candidate_id` | usually null on individual donations |
| `candidate_name` | `candidate_name` | |
| `filing_url` | `pdf_url` | |

Useful but not currently captured: `entity_type` (IND/ORG/COM), `is_individual` (bool), `fec_election_year`. Add columns only if signal warrants.

**Schedule B (PAC outflows) — NOT used.** Mixes vendor payments + event sponsorships + candidate giving (Lockheed PAC has rows like "ALABAMA STATE SOCIETY $10K"). For PAC→candidate flow, use Schedule A with `contributor_id`.

**Gotchas (verified 2026-05-01):**
1. **`candidate_id` filter on `/schedule_a/` is dropped silently** — see table above. Always use `committee_id` (the PCC). The bogus-ID test is the canonical confirmation.
2. **`candidate_id` field in Schedule A response sometimes contains committee-shaped IDs** (e.g. `C00484535`). The schema FK `campaign_donations.candidate_id → legislator_fec_ids.fec_candidate_id` rejects these. Production ingest pre-loads the valid candidate-id set (~1,713) and soft-NULLs unknown values in `_to_donation_row`. See `valid_candidate_ids` in `fec/ingest.py`.
3. **Rate limit is per-minute** — 60/min, not 7,200/hr. Bursting 100 calls in 30s triggers 429 even if hourly budget unused. Throttle is enforced inside `PoliticalHTTPClient` and only fires on actual network calls (cache hits skip — see migration 015 + `_client.py`).
4. **Old `legislator_fec_ids` rows are historical** — a member can have multiple FEC candidate IDs across cycles (e.g. Pelosi has 4 committees from 1986→2024). Mode B should iterate ALL of a member's candidate IDs, not just the most recent.
4. **`min_amount` is dollar-rounded** — pass `1000` not `1000.00`. `min_amount=999` returns same row count as `min_amount=1000` for the $1K-aggregation threshold.
5. **PAC name lookup is fragile** — "BOEING COMPANY POLITICAL ACTION" returns nothing; "BOEING COMPANY PAC" works. The `_resolver` ticker→PAC map needs multiple alias attempts per ticker (or use the corporate-PAC sweep: `committee_type=Q&organization_type=C`).
6. **Cursor params are stringly-typed** — `last_index` is a 19-digit string, not an int. Don't cast.
7. **Pelosi's PCC `C00213512` shows cycles `[1986, 1988, 1990]`** in `/candidate/.../committees/` despite still receiving donations in 2024 — the `cycles` field reflects when the committee was first created, not its activity. Don't filter committees by `cycles` for live PCC selection; filter by `designation='P'`.

**Reference**: full probe results in `playground/explore/fec/NOTES.md` (gitignored, but mirrored here as the system-of-record).

#### FEC corp-PAC ticker resolution (verified 2026-05-01)

The resolver normalizes `committee.name` → SEC ticker via a **conservative cascade tuned for FEC PAC names**. Wrong classification corrupts the alpha signal worse than no classification, so the cascade prefers no-match over guessing.

**Pre-normalization** (`normalize_pac_name`) strips PAC vocabulary BEFORE running the standard SEC normalizer:
- Parentheticals `(LMPAC)`, `(AAPAC)`, `(MSVPAC)`
- Tokens ending in `pac` (FEDPAC, MSVPAC, ARTPAC, GOPAC) — but NOT words merely containing `pac` like `pacific`, `impact`
- Core PAC vocabulary: `political action committee fund foundation`
- Sponsorship descriptors: `employees stakeholders members voluntary sponsored associates partners`
- Governance descriptors: `good government federal nonpartisan bipartisan partisan`
- Vehicle types: `trust trustees connect forward activity activities leadership`
- Aliasing markers: `fka aka nka formerly known as`
- Common short tokens in PAC titles: `for to with by us usa`

**Cascade** (stops at first hit):

| Step | Confidence | Description |
|---|---:|---|
| 1. Manual alias (full PAC-stripped name) | 1.00 | `configs/reference/contractor_aliases.yaml` exact match |
| 2. SEC exact normalized name | 0.95 | PAC-stripped == SEC company normalized |
| 3. **Longest-prefix alias hit** (try 3, 2, then 1 leading tokens) | 0.90 | Catches "BLACKROCK FUNDS SERVICES GROUP" → BLK. Single-token aliases require ≥4 chars to prevent "ge"/"f" collisions. |
| 4. Verified prefix match | 0.85 | 2-token prefix matches AND **all SEC-name tokens are present** in the PAC-stripped name (prevents "First Interstate Texas" → FIBK) |
| 5. No match | — | Logged to `data/political/_state/_learn_queue.csv` for manual or LLM review |

**Dropped from earlier resolver**: loose 2-token prefix (without token-coverage check), fuzzy Jaccard. Both produced too many false positives on FEC PAC names.

**Empirical results (2026-05-01)** on a 3,473-row corp-PAC sweep:

| Match kind | Count | Examples |
|---|---:|---|
| `exact` | 347 | LOCKHEED MARTIN, COCA-COLA, AT&T |
| `alias` | 102 | MICROSOFT (manual), CHEVRON (manual), TESLA-aliases |
| `alias_prefix` | 83 | BLACKROCK FUNDS SERVICES GROUP → BLK; METLIFE EMPLOYEES PARTICIPATION → MET |
| `verified_prefix` | 58 | DEERE & COMPANY ILLINOIS → DE (full SEC tokens "deere" present in PAC-norm) |
| `none` | 2,730 | private companies, foreign parents, defunct entities, trade associations |
| **Total resolved** | **590** | |

**Priority-ticker coverage**: 71/77 alpha-relevant tickers represented. The 6 not represented (AAPL, NVDA, SLB, TSLA, BABA, PGR) are **genuinely absent** — those companies don't run federal corporate PACs (or use non-`Q+C` classifications). Verified via raw name-search across the unmatched 2,730 rows.

**Re-resolution mode**: `python -m factorlab.sources.political.fec.ingest --re-resolve` re-runs the resolver over `fec_committees` in-place without API calls. Use after tuning the alias file or normalizer to update `sponsor_company_ticker` without re-fetching.

---

## Part 2 — Aggregator Providers (Pros/Cons)

### 2A. Senate Stock Watcher (FROZEN MIRROR — historical 2012-2020 only)

> **Verified frozen 2026-04-29.** S3 buckets return HTTP 403 across all known regions/paths. `senatestockwatcher.com` DNS does not resolve. GitHub data mirror frozen with last commit 2021-03-16. Upstream creator moved to AnythingLLM. **Do NOT use as a live source.** Use as a one-time historical backfill for the pre-eFD-direct era; once ingested, the daily/weekly orchestrator should not re-pull it.

**Live-DB row count (2026-05-03): 12,685 rows, 2012-06-14 → 2020-12-02.**
6,217 of those (49%) have a resolved ticker. The mirror covers more years
than the doc historically claimed — earliest tx date is 2012-06, latest is
2020-12-02. The Senate eFD direct scrape (`senate_efd_ptr`) takes over
from 2019 onward. The 2019-2020 overlap window produces ~330 logical
duplicates which are collapsed by the
[`legislator_trades_dedup`](#cross-source-dedup-view-2026-05-03) view
(senate_efd_ptr wins precedence; SSW rows lose).

The `senate-stock-watcher-data` GitHub mirror remains accessible at `raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/master/aggregate/all_transactions.json`. It is the only free path to senate trade data for years 2012-2018.

**Schema observed in the mirror (note divergence from doc's earlier claim):**
```json
{
  "transaction_date": "11/10/2020",  // MM/DD/YYYY
  "owner": "Spouse",                  // Joint, Spouse, Self, Child, N/A
  "ticker": "BYND",                   // 25.3% are "--" or missing
  "asset_description": "Beyond Meat, Inc.",
  "asset_type": "Stock",
  "type": "Sale (Full)",              // Purchase, Sale (Full), Sale (Partial), Exchange, N/A
  "amount": "$50,001 - $100,000",
  "comment": "--",
  "senator": "Ron L Wyden",
  "ptr_link": "https://efdsearch.senate.gov/..."
}
```
**Fields the older doc claimed but that don't exist in the JSON:** `disclosure_date`, `senator_id`, `filed_after_date`. Synthesize `filing_date` as `transaction_date + 30d` for backfill (mid of 0-45-day STOCK Act lag). Match to bioguide via fuzzy `last_name`-in-`senator` against `unitedstates/congress-legislators`.

**Original (now-stale) reference table preserved for context:**

| Field | Detail |
|-------|--------|
| URL (dead) | https://senatestockwatcher.com |
| API (dead) | Free, no auth, no rate limit documented |
| Coverage | Senate only, 2014-2019 actually populated |
| Creator | Timothy Carambat (now focused on AnythingLLM) |

**Endpoints:**
```
GET /api/trades.json                  # All trades, all senators
GET /api/senators.json                # List of senators with trade counts
GET /api/senator/{first-last}.json    # Per-senator (lowercase hyphenated)
GET /api/ticker/{TICKER}.json         # Per-ticker
```

**JSON schema (per trade):**
```json
{
  "transaction_date": "2023-08-15",
  "ticker": "MSFT",
  "asset_description": "Microsoft Corporation",
  "asset_type": "Stock",
  "type": "Purchase",
  "amount": "$1,001 - $15,000",
  "senator": "John Doe",
  "senator_id": "john-doe",
  "filed_after_date": "2023-08-28",
  "ptr_link": "https://efts.senate.gov/...",
  "disclosure_date": "2023-09-01",
  "comment": null,
  "owner": "Self"
}
```

**Pros:** Free, no auth, daily updates, captures spouse/dependent (`owner` field), links to source PDFs
**Cons:** Senate only, ~15-25% of tickers unresolved (`"--"`), amendments are additive-only (need dedup), no committee data, no House coverage

**GitHub data repo:** `github.com/timothycarambat/senate-stock-watcher-data` — clone for bulk historical backfill. JSON files per senator in `transaction_report_data/`.

**Dedup key:** `(senator_id, transaction_date, ticker, type, disclosure_date)` — keep most recent by disclosure_date.

### 2B. House Stock Watcher (DEAD)

| Field | Detail |
|-------|--------|
| Status | **BROKEN since mid-2023** — S3 backend returns HTTP 403 |
| Historical | GitHub repo has data through mid-2023 |
| Alternative | Use House Clerk XML ZIPs directly, or FMP/Quiver for House |

### 2C. Quiver Quantitative (BEST PAID OPTION — $10-75/mo)

| Field | Detail |
|-------|--------|
| URL | https://quiverquant.com |
| API | REST at `api.quiverquant.com/beta/`, Bearer token auth |
| Python SDK | `pip install quiverquant` — returns pandas DataFrames |
| Coverage | Both chambers, 2014+, gov contracts, lobbying, insider trades |

**Key endpoints:**
```python
quiver = quiverquant.quiver(api_key="KEY")

# Congressional trades
df = quiver.congress_trading()                    # All recent
df = quiver.congress_trading(ticker="MSFT")       # By ticker
df = quiver.congress_trading(representative="Nancy Pelosi")  # By member

# Government contracts (critical for small-cap thesis)
df = quiver.gov_contracts(ticker="LMT")

# Lobbying
df = quiver.lobbying(ticker="AMZN")

# Committee assignments (CURRENT only)
df = quiver.senate_committees()
df = quiver.house_committees()
```

**Response fields (congress_trading):**
`Ticker`, `Representative`, `Transaction`, `Amount` (range string), `Date` (trade date), `House` (chamber), `Party`, `State`, `District`, `Range`, `ReportDate`

**Unique value — the three-way cross-reference:**
```
Signal = Committee_Member_Bought_Ticker
       AND Ticker_Has_Active_Gov_Contracts_With_Committee_Jurisdiction
       AND Ticker_Is_Lobbying_That_Committee
```
This dramatically reduces false positives vs simple trade following.

**Pros:** Single API for trades + contracts + lobbying + committees. Python SDK. Best for quant pipelines.
**Cons:** Free tier very limited (~50 req/day, 1yr history). Historical committee assignments not available (only current). Subsidiary ticker mapping imperfect for small caps. Options trades inconsistently normalized.

**Pricing:** Free (limited) → ~$10/mo (PyPI package) → ~$50-75/mo (premium full history). Academic access available case-by-case.

### 2D. Finnhub (FREE — but only `/stock/usa-spending` works)

| Field | Detail |
|-------|--------|
| URL | https://finnhub.io |
| API | REST, free key, 60 req/min (no daily cap) |

**Verified empirically 2026-04-29:**

| Endpoint | Free tier? | Notes |
|---|---|---|
| `/stock/usa-spending` | ✅ **YES** | Ticker-keyed govt contracts. Probed: LMT=932, RTX=2000 (capped), PLTR=434, NOC=2000. Schema includes `recipientParentName, totalValue, actionDate, performanceStartDate/EndDate, awardingAgencyName, awardingSubAgencyName, naicsCode, awardDescription, performanceCongressionalDistrict`. |
| `/stock/congressional-trading` | ❌ Paid | Returns HTTP 403 on free tier (51-byte error body) |
| `/stock/lobby` | ❌ Paid | Returns 6.2KB HTML paywall page (not the documented JSON) |
| `/stock/insider-transactions` | ✅ Likely | SEC Form 4 — corporate insiders, NOT politicians |

**Use it for:** the contracts cross-reference layer (replaces direct USASpending search for ticker-keyed lookups). Cache per-ticker JSON; ~1 sec/call respects 60/min budget.

**Don't use it for:** congressional trades or lobbying — both moved behind the paywall. We get those from direct PTR/eFD/LDA scraping instead.

**Original (now-stale) section preserved for context:** earlier draft claimed `/stock/congressional-trading` and `/stock/lobby` were free. They are not.

### 2E. FMP — Financial Modeling Prep (BACKUP)

| Field | Detail |
|-------|--------|
| URL | https://financialmodelingprep.com |
| API | REST, free key, **250 calls/day** (tight) |
| Coverage | Both chambers (separate endpoints), 2013+ |

**Endpoints:**
```
GET /api/v4/senate-trading?symbol=AAPL&apikey=KEY
GET /api/v4/house-trading?symbol=AAPL&apikey=KEY
GET /api/v4/senate-trading-rss-feed?page=0&apikey=KEY    # Latest filings chronologically
```

**Pros:** Both chambers, RSS feed endpoint efficient for daily batch
**Cons:** 250/day free cap too tight for batch. No date filtering (client-side only). No party field. No committee data. No per-politician queries.

### 2F. Capitol Trades (MANUAL RESEARCH ONLY)

| Field | Detail |
|-------|--------|
| URL | https://capitoltrades.com |
| API | **No public API** — React frontend with Cloudflare protection |
| Cost | Free browse / $9.99/mo premium |

**Best feature:** Committee-match analytics + performance tracking (premium). Historical committee assignments maintained.

**Do NOT scrape.** Cloudflare JS challenge, React client-side rendering, ToS prohibits automation. Use for manual validation only.

### 2G. Unusual Whales (PREMIUM — OPTIONS OVERLAY)

| Field | Detail |
|-------|--------|
| URL | https://unusualwhales.com/politics |
| API | REST at `api.unusualwhales.com/docs`, Bearer token |
| Cost | $100-200/mo for API access |

**Unique value:** Cross-references congressional trades with unusual options flow on same tickers. Senator buys + options sweep = compound signal.

**Limitation for small caps:** Options overlay degrades below ~$1B market cap (thin/no options markets on true small caps). The congressional trade data itself is more relevant.

**Endpoints:**
```
GET /api/congress/trades?ticker=AAPL&chamber=senate&date_from=2024-01-01
GET /api/congress/politicians
GET /api/congress/politicians/{id}/trades
GET /api/congress/tickers/{ticker}/trades
```

### 2H. Newer/Alternative Sources

| Source | Cost | API | Notes |
|--------|------|-----|-------|
| **Lambda Finance** (lambdafin.com) | Free 50 req/mo → $19/mo | REST | Clean, newer. Has `owner` field (self/spouse). MCP integration for Claude. |
| **Meridian Finance** (meridianfin.io) | Free tier | REST (150+ endpoints) | "Conviction Score" combining dark pool + congressional + insider. Good for mid-cap cross-signal. |
| **OpenSecrets** (opensecrets.org) | Bulk data free / API discontinued Apr 2025 | Bulk CSV download only | Campaign finance + net worth estimates. Good for cross-ref donations → trades. |
| **Apify Scrapers** (apify.com) | Pay-per-run | Cloud actors | Pre-built scrapers for Senate eFD, Capitol Trades, House Clerk. Good for occasional batch. |
| **InsiderFinance** (insiderfinance.io) | $40-80/mo | No public API | SEC Form 4 + congressional trades combined dashboard. No programmatic access. |

### 2I. GitHub Open-Data Repos

| Repo | Type | Status | Best For |
|------|------|--------|----------|
| `timothycarambat/senate-stock-watcher-data` | Data (JSON) | Active (auto-commits) | Senate historical backfill |
| `timothycarambat/house-stock-watcher-data` | Data (JSON) | Dead (stale mid-2023) | House historical through 2023 |
| `unitedstates/congress-legislators` | YAML | Active, canonical | Legislator master, ID crosswalk, committee membership |
| `jeremiak/congress-trading-data` | Data | Varies | Cross-validation |
| `ncerovac/nancy` | Telegram bot | Active, MIT | Multi-source notification reference, Railway-deployable |
| `abdkhan-git/StockInsightsTracker` | Scraper | MIT | LLM-based PDF extraction pattern |

---

## Part 3 — Schema (canonical)

### `alt_political_us.legislator_trades`
```sql
trade_id           uuid PRIMARY KEY DEFAULT gen_random_uuid()
chamber            text NOT NULL              -- 'senate', 'house'
legislator_id      text NOT NULL              -- bioguide id (canonical)
legislator_name    text NOT NULL
party              text                       -- 'D', 'R', 'I'
state              char(2)
filer_type         text                       -- 'self', 'spouse', 'dependent_child', 'joint'
transaction_date   date                       -- when trade occurred
filing_date        date                       -- when disclosed (signal lag matters!)
ticker             text                       -- resolved ticker (NULL until resolved)
asset_name_raw     text NOT NULL              -- EXACTLY as filed (preserve for audit)
security_id        uuid REFERENCES ref.securities    -- nullable until resolved
asset_type         text                       -- 'Stock', 'Stock Option', 'ETF', 'Corporate Bond', etc.
transaction_type   text                       -- 'Purchase', 'Sale (Full)', 'Sale (Partial)', 'Exchange'
amount_min         bigint                     -- parsed range lower bound
amount_max         bigint                     -- parsed range upper bound (NULL for "Over $50M")
amount_mid         bigint                     -- (min+max)/2 for modeling
filing_url         text
filing_id          text                       -- DocID (House) or eFD ID (Senate) for dedup
source             text NOT NULL              -- 'house_clerk', 'senate_efd', 'senate_stock_watcher', 'quiver', etc.
raw_payload_id     uuid
fetched_at         timestamptz NOT NULL DEFAULT now()
as_of_time         timestamptz NOT NULL       -- POINT-IN-TIME — when WE knew
-- Dedup
UNIQUE (chamber, legislator_id, transaction_date, ticker, transaction_type, filing_date)
```

### `alt_political_us.legislator_committees`
```sql
legislator_id      text NOT NULL              -- bioguide id
committee_code     text NOT NULL              -- Thomas ID (e.g., 'SSAS', 'SSFI')
committee_name     text NOT NULL
chamber            text NOT NULL              -- 'senate', 'house'
role               text                       -- 'chair', 'ranking_member', 'member'
subcommittee       text                       -- NULL for full committee membership
valid_from         date NOT NULL
valid_to           date                       -- NULL = current
PRIMARY KEY (legislator_id, committee_code, valid_from)
```

### `alt_political_us.legislators`
```sql
legislator_id      text PRIMARY KEY           -- bioguide id
first_name         text NOT NULL
last_name          text NOT NULL
party              text
state              char(2)
chamber            text                       -- current chamber
in_office          boolean DEFAULT true
govtrack_id        text
opensecrets_cid    text
fec_id             text
net_worth_low      bigint                     -- from eFD annual disclosures
net_worth_high     bigint
net_worth_year     int
```

### `alt_political_us.committee_sector_map`
```sql
committee_code     text NOT NULL              -- Thomas ID
gics_code          text NOT NULL              -- GICS industry code
signal_strength    text                       -- 'extreme', 'very_high', 'high', 'medium', 'low'
rationale          text
PRIMARY KEY (committee_code, gics_code)
```

### `alt_political_us.gov_contracts`
```sql
contract_id        text PRIMARY KEY
ticker             text                       -- mapped (may be NULL for unmapped companies)
company_name       text NOT NULL
agency             text
description        text
amount             numeric
award_date         date
period_start       date
period_end         date
source             text DEFAULT 'usaspending'
fetched_at         timestamptz NOT NULL DEFAULT now()
```

### `alt_political_us.lobbying`
```sql
lobbying_id        uuid PRIMARY KEY DEFAULT gen_random_uuid()
ticker             text
client             text NOT NULL
registrant         text
amount             numeric                    -- quarterly spend
filing_period      text                       -- e.g., '2024 Q2'
filing_date        date
specific_issue     text                       -- free-text from LDA filing
senate_committee   text                       -- when disclosed
house_committee    text                       -- when disclosed
source             text DEFAULT 'quiver'
fetched_at         timestamptz NOT NULL DEFAULT now()
```

---

## Part 4 — Amount Range Parsing

STOCK Act disclosures use ranges, not exact amounts. This is permanent (by law).

```python
AMOUNT_RANGES = {
    "$1,001 - $15,000":            (1_001,      15_000),
    "$15,001 - $50,000":           (15_001,     50_000),
    "$50,001 - $100,000":          (50_001,     100_000),
    "$100,001 - $250,000":         (100_001,    250_000),
    "$250,001 - $500,000":         (250_001,    500_000),
    "$500,001 - $1,000,000":       (500_001,    1_000_000),
    "$1,000,001 - $5,000,000":     (1_000_001,  5_000_000),
    "$5,000,001 - $25,000,000":    (5_000_001,  25_000_000),
    "$25,000,001 - $50,000,000":   (25_000_001, 50_000_000),
    "Over $50,000,000":            (50_000_001, None),
}

def parse_amount(amount_str: str) -> tuple[int | None, int | None, int | None]:
    lo, hi = AMOUNT_RANGES.get(amount_str.strip(), (None, None))
    mid = (lo + hi) // 2 if lo and hi else lo
    return lo, hi, mid
```

**Signal note:** Amount relative to net worth matters. A $15K-$50K buy from a senator worth $500K = conviction. Same range from one worth $50M = noise. Join against `alt_political_us.legislators.net_worth_*`.

---

## Part 5 — Committee-to-Sector Mapping (Alpha Core)

Signal strength: how likely committee membership creates information asymmetry on sector-specific stock trades.

### Senate Committees

| Committee | Thomas ID | Signal | Key Sectors | Example Tickers |
|-----------|-----------|--------|-------------|-----------------|
| **Finance** | SSFI | EXTREME | Pharma (Medicare pricing), ALL (tax rates), REITs, tariff-exposed | PFE, MRK, ABBV, LLY, BMY, HCA, UNH, HUM |
| **Appropriations** | SSAP | EXTREME | Via 12 subcommittees — Defense (LMT, RTX, NOC), HHS (pharma), Energy (utilities) | Per subcommittee |
| **Armed Services** | SSAS | VERY HIGH | Aerospace & Defense, cyber, military IT | LMT, RTX, NOC, GD, BA, L3H, LDOS, SAIC, CACI, BAH |
| **Banking, Housing** | SSBK | VERY HIGH | Banks, insurance, fintech, REITs, GSEs | JPM, BAC, GS, MS, WFC, C, BLK, SCHW, ICE, CME |
| **Energy & Natural Resources** | SSEG | HIGH | Oil/gas E&P, utilities, mining, nuclear | XOM, CVX, COP, NEE, DUK, FCX, NEM, CEG |
| **Health (HELP)** | SSHR | HIGH | Pharma, biotech, hospitals, med devices, PBMs | PFE, AMGN, REGN, VRTX, ISRG, MDT, ABT, CVS |
| **Commerce, Science, Transport** | SSCM | HIGH | Telecom, airlines, auto, internet, rail | T, VZ, CMCSA, GOOGL, META, DAL, UAL, GM, TSLA |
| **Environment & Public Works** | SSEV | HIGH | Waste mgmt, water, chemicals, construction | WM, RSG, AWK, DD, DOW, VMC, MLM |
| **Agriculture** | SSAF | MEDIUM-HIGH | Ag commodities, fertilizers, farm equipment, food retail | ADM, BG, MOS, NTR, CF, DE, AGCO, KR |
| **Judiciary** | SSJU | MEDIUM | Big tech (antitrust), prisons, IP-heavy pharma | GOOGL, META, AMZN, AAPL, MSFT, CXW, GEO |
| **Intelligence** | SLIN | MEDIUM | Defense/intel contractors (classified) | PLTR, LDOS, BAH, SAIC, CACI |
| **Foreign Relations** | SSFR | MEDIUM | Arms exporters, sanctions-exposed | LMT, RTX (FMS), oil majors |
| **Veterans' Affairs** | SSVA | MEDIUM | VA healthcare contractors | UNH (VA managed care), CVS, HII |
| **Homeland Security** | SSGA | MEDIUM | Cybersecurity, border tech | CRWD, PANW, FTNT, LDOS |

### House Committees (parallel jurisdiction)

| Committee | Key Sectors | Example Tickers |
|-----------|-------------|-----------------|
| **Ways and Means** | Tax, trade, Medicare | Same as Senate Finance |
| **Appropriations** | Same subcommittee structure as Senate | Same |
| **Armed Services** | Defense | Same as Senate Armed Services |
| **Financial Services** | Banks, crypto, insurance | Same as Senate Banking |
| **Energy and Commerce** | Healthcare + telecom + energy (broadest House committee) | Combined SSEG + SSHR + SSCM |
| **Agriculture** | Ag, CFTC oversight | Same as Senate Ag |
| **Judiciary** | Antitrust, immigration, IP | Same as Senate Judiciary |

---

## Part 6 — Pipeline Architecture

### Phase 1 — Free, Self-Sovereign (build now, $0)

```
┌──────────────────────────────────────────────────────────────────┐
│  HISTORICAL BACKFILL (one-time)                                  │
├──────────────────────────────────────────────────────────────────┤
│ House (2014-today):                                              │
│   For each year, GET /public_disc/financial-pdfs/{Y}FD.ZIP       │
│   → parse {Y}FD.xml for (DocID, Year, FilingType='P', filer)     │
│   → for each DocID, GET /public_disc/ptr-pdfs/{Y}/{DocID}.pdf    │
│   → pdfplumber → row regex → AMOUNT_BUCKETS lookup               │
│   → INSERT alt_political_us.legislator_trades                       │
│                                                                  │
│ Senate 2014-2019 (frozen):                                       │
│   Pull GitHub mirror's all_transactions.json (8,350 rows)        │
│   → field-rename + synthesize filing_date = txn + 30d            │
│   → INSERT alt_political_us.legislator_trades                       │
│                                                                  │
│ Senate 2020-today:                                               │
│   Direct eFD scraper: agreement-form POST → cookie session       │
│   → /search/report PTR list by date window                       │
│   → for each PTR, fetch + parse PDF                              │
│   → INSERT alt_political_us.legislator_trades                       │
│                                                                  │
│ Members + committees:                                            │
│   GET raw legislators-current.yaml + committees-current.yaml     │
│       + committee-membership-current.yaml                        │
│   → INSERT alt_political_us.legislators / .legislator_committees    │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  DAILY INCREMENTAL (06:00 UTC)                                   │
├──────────────────────────────────────────────────────────────────┤
│ House: Poll ASPX search filtered by FilingDate >= last_run-1d    │
│        + re-fetch current-year ZIP weekly for amendments         │
│ Senate eFD: same — search by date window since last_run          │
│ Both: dedup on UNIQUE (chamber, legislator_id, transaction_date, │
│       ticker, transaction_type, filing_date)                     │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  ENRICHMENT (background)                                         │
├──────────────────────────────────────────────────────────────────┤
│ Ticker resolution:                                               │
│   asset_name_raw → EODHD symbol search → ref.securities          │
│                                                                  │
│ Committee tagging:                                               │
│   Join legislator_id → legislator_committees → committee_sector_ │
│   map → signal_strength + role_weight → derived.political_       │
│   features                                                       │
│                                                                  │
│ Govt contracts cross-ref:                                        │
│   For each unique ticker, GET Finnhub /stock/usa-spending        │
│   (cached per-ticker JSON, free key, 60/min)                     │
│   → INSERT alt_political_us.gov_contracts                           │
│                                                                  │
│ Forward returns:                                                 │
│   JOIN trades → market.prices at filing_date + N days            │
│   for N ∈ {5, 30, 90} → performance tracking                     │
└──────────────────────────────────────────────────────────────────┘
```

### Phase 2 — Optional paid uplift (only if needed)

- **Quiver Quantitative** ($10/mo): saves the eFD scraper effort and gives lobbying with ticker mapping. Free tier (~50 req/day) is too tight for batch.
- **Unusual Whales** ($100-200/mo): options-flow overlay; degrades for sub-$1B caps so likely skip.

### Phase 3 — Cabinet / executive branch (deferred)

- **OGE Form 278** (Office of Government Ethics): cabinet members + senior execs file the same kind of PTRs, but only as PDFs through a clunky search UI; no API. Build this only if v1 senate+house value is proven.

---

## Part 7 — Signal Construction

### Core factor: committee-relevant insider signal

```
Event date = filing_date (when it became public, NOT transaction_date)

Signal = (buy_volume - sell_volume) by ticker
         OVER trailing 30-day window from filing_date
         WEIGHTED BY:
           - amount_mid (larger = more conviction)
           - committee_relevance (1.0 if relevant committee, 0.3 otherwise)
           - role_weight (2.0 for chair/ranking, 1.0 for member)
           - amount_mid / net_worth (conviction relative to wealth)
```

### The triple-conjunction small-cap signal

```sql
SELECT DISTINCT t.ticker, t.legislator_name, t.filing_date, t.amount_mid,
       c.committee_name, c.role,
       g.amount as contract_amount, g.agency,
       l.amount as lobby_spend
FROM alt_political_us.legislator_trades t
JOIN alt_political_us.legislator_committees c
  ON t.legislator_id = c.legislator_id
  AND t.transaction_date BETWEEN c.valid_from AND COALESCE(c.valid_to, '9999-12-31')
JOIN alt_political_us.committee_sector_map csm
  ON c.committee_code = csm.committee_code
JOIN ref.securities s
  ON t.security_id = s.security_id
  AND s.gics_code LIKE csm.gics_code || '%'
LEFT JOIN alt_political_us.gov_contracts g
  ON t.ticker = g.ticker
  AND g.award_date BETWEEN t.transaction_date - INTERVAL '180 days'
                       AND t.transaction_date + INTERVAL '90 days'
LEFT JOIN alt_political_us.lobbying l
  ON t.ticker = l.ticker
  AND l.filing_date BETWEEN t.transaction_date - INTERVAL '180 days'
                        AND t.transaction_date + INTERVAL '90 days'
WHERE t.transaction_type = 'Purchase'
  AND csm.signal_strength IN ('extreme', 'very_high', 'high')
  AND s.market_cap < 500000000  -- small cap filter
ORDER BY t.filing_date DESC;
```

---

## Part 8 — Edge Cases & Gotchas

- **Reporting lag** — up to 45 days. The `filing_date` IS the signal date, not `transaction_date`.
- **Amount ranges, not exact** — permanent STOCK Act limitation. Use midpoint with care, or treat as ordinal.
- **Spouses & dependents** — many trades filed under spouse. Tag via `filer_type`, treat as legitimate signal.
- **Late / amended filings** — common. Store both; `as_of_time` distinguishes versions. Dedup on composite key, keep latest by `filing_date`.
- **Ticker resolution** — filers write inconsistently: "Apple Inc." / "AAPL" / "APPLE INC COM". Store raw in `asset_name_raw`, resolve to `security_id` as background enrichment via EODHD symbol search.
- **PDF parsing for Senate** — avoid where possible. Use Senate Stock Watcher JSON. For gap-filling: `pdfplumber` → OCR fallback → LLM extraction as last resort.
- **Bulk transactions** — one PTR can list many trades. Preserve as separate rows, link via `filing_id`.
- **Pelosi premium** — high-profile names attract social attention. Copy-trading signals may be crowding-driven decay, not alpha.
- **Options trades** — "Purchase" of a put option is bearish. The raw `transaction_type` field says "Purchase" regardless. `asset_type` disambiguation required.
- **Subsidiary mapping** — small-cap companies winning contracts as subsidiaries of larger entities may not map to their parent ticker. Manual override table needed.
- **House Stock Watcher AND Senate Stock Watcher are both dead** (2026-04 verified). Both S3 buckets return 403. Senate GitHub mirror frozen 2021-03-16 with data ending 2019. Don't depend on either as a live source.
- **House Clerk annual XML is filing-INDEX only** — has no transaction-level fields. Earlier doc draft was wrong about `Ticker`/`AssetName`/etc. living in the XML. Per-PTR PDFs hold the trades.
- **Finnhub free tier excludes congressional-trading and lobby** — both endpoints return 403/HTML paywall. Only `/stock/usa-spending` is genuinely free.
- **PTR parser hit rate** — naive regex catches ~37% of transactions per PTR. Tuning + table-aware extraction gets to ~80%. Some "0-trade" PTRs are legitimate no-trade reporting.
- **Senate eFD agreement-page session expires** on inactivity — scraper must auto-detect 302 to `/search/home/` and re-POST.
- **Filer name quirks** — XML returns honorifics inserted as middle names (`"Marjorie Taylor Mrs Greene"`, `"Mark Dr Green"`). Bioguide fuzzy match must be permissive on first/middle.
- **Bioguide match for retired senators fails by design** — `legislators-current.yaml` only carries currently-serving members. Historical 2014-2019 trades by retired senators (Perdue, Carper, Roberts, Loeffler, etc.) won't have a current committee. ~45% of historical Senate Stock Watcher rows fall here. Either (a) add `legislators-historical.yaml` from the same repo, or (b) accept the gap and act only on currently-serving members.

---

## Part 9 — Reference Data Sources

| Source | URL | Use |
|--------|-----|-----|
| Bioguide IDs | https://bioguide.congress.gov/ | Canonical legislator identifier |
| unitedstates/congress-legislators | github.com/unitedstates/congress-legislators | YAML: legislator master, ID crosswalk, committee membership |
| Congress.gov API | api.congress.gov/v3/ | Current member + committee data |
| OpenSecrets | opensecrets.org/bulk-data | Net worth estimates, campaign finance, bulk CSV |
| GovTrack | govtrack.us/data/ | Voting records, ideology scores, bill data |
| USASpending | api.usaspending.gov | Federal contract awards |
| Senate LDA | lda.senate.gov/system/public/ | Lobbying disclosure filings |

---

## Part 10 — Resolved Questions

- [x] **Direct parsing vs. paid aggregator for v1?** → Hybrid: Senate Stock Watcher JSON (free) + House Clerk XML (free) for trades. `unitedstates/congress-legislators` for committee data. Add Quiver ($10/mo) when ready for contracts/lobbying cross-reference.
- [x] **OCR pipeline budget?** → Avoid. Use Senate Stock Watcher JSON to skip PDF parsing entirely for v1. OCR only needed for gap-filling edge cases.
- [x] **Extend to lobbying and government contracts?** → Yes, in `alt_political_us` schema. Critical for the small-cap thesis. Phase 2 via Quiver or Finnhub free tier.

## Open Questions

- [ ] Historical committee assignments: build from git history of `unitedstates/congress-legislators`? This is an open research gap.
- [ ] Comparable EU (MEPs) and India (parliamentary) disclosures — are they public? Phase 6+ scope.
- [ ] PEP scope creep — governors, federal judges, fed governors? Not in v1.
- [ ] Campaign finance cross-reference: "Company X donated to Senator Y" + "Senator Y bought Company X stock" — via OpenSecrets bulk data?
