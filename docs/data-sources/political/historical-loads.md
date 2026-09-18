# Political Data — Historical Loads & One-Off Recoveries

> Status: `[reference]` — institutional history of one-shot political backfill / rescue scripts. Last updated: 2026-05-08.

This doc preserves the **WHY** behind one-shot scripts that built the
`alt_political_us` schema's historical depth. The scripts themselves are
archived under `scripts/archive/` (see status column); the upstream logic
they produced now lives in `src/factorlab/sources/political/`. Read this
when investigating questions like "where did the paper-PTR placeholder rows
come from?", "what fixed the 2024-2026 NULL transaction_type wedge?", or
"can I rerun the House Clerk historical load?".

For the running pipeline (daily / weekly / hourly), see
[`pipeline.md`](pipeline.md). For schema design, see
[`docs/architecture/database.md`](../../architecture/database.md)
and [`senator-trades.md`](senator-trades.md).

---

## Cross-cutting principles (preserved across all loads)

The following invariants hold across every historical campaign and the
current ingest. They are encoded in code in
`src/factorlab/sources/political/`. New work in this domain must keep them.

1. **4-tier classification** — every PTR is one of:
   - **Tier 1**: electronic + valid trades + valid bioguide + valid instrument → AUTO-INSERT
   - **Tier 2**: electronic + trades but a quality gate failed → CSV review queue
   - **Tier 3**: paper-scan (heuristic) → placeholder row
   - **Tier 4**: text-rich but parser produced 0 trades / errored → CSV review queue

2. **Paper-scan heuristic** — `cleaned text < 200 chars OR zero TRADE_ANCHOR_RE
   matches`. Validated zero false-positives on a 1,869-PDF sample.

3. **Strict-NULL policy** — `transaction_type`, `filer_type`, `asset_type_code`
   are nullable on `legislator_trades`. Sources write `None` for unknowns
   rather than fabricating defaults (`'purchase'` / `'self'` / `'OT'`).

4. **Bioguide matching stages** — Stage 0 is `legislator_aliases` (manual
   overrides like Van Taylor → T000479); Stage 1 is `StrictBioguideMatcher`
   (exact normalized name + term-overlap by trade_date). No fuzzy
   fallback beyond these.

5. **Conflict key for upsert** — every legislator_trades row is keyed by
   `(endpoint_id, country_code, chamber, filing_id, transaction_date,
   asset_name_raw, transaction_type, amount_str)`. Paper-scan placeholders
   carry distinct `asset_name_raw` (e.g. `[PAPER PTR — N pages — OCR pending]`)
   so they coexist with later OCR'd trade rows.

6. **Idempotency** — every campaign defensively dedupes on the conflict key
   inside a batch before calling `fast_upsert`. All scripts here are safe
   to re-run.

---

## Campaigns

Each section: **Purpose / Motivation / Approach / Outcome / Status / Where the
logic lives now.**

### 1. House Clerk historical load (2013–2026)

- **Script**: `scripts/archive/load_house_clerk_historical.py`
- **Purpose**: Tiered, parallel batch load of every House PTR PDF from
  2013-01-01 through 2026 into `alt_political_us.legislator_trades`.
- **Motivation**: A reproducible, defensible 14-year backfill. Manual
  inspection of a few hundred PDFs revealed distinct quality patterns:
  some parsed cleanly; some had free-text contamination that polluted
  `asset_name_raw`; many were paper scans (OCR pending); some errored
  outright. Needed to land safe rows immediately while capturing
  ambiguous ones for expert review without blocking the pipeline.
- **Approach**: `multiprocessing.Pool` (cpu_count − 1) parses PDFs in
  parallel. A single-process classifier then applies the 4-tier gate
  per filing. Header-pollution markers (`"filer information"`,
  `"transactions"`, `"id owner"`, etc.) flag bad asset names. Ticker
  validation regex `^[A-Z][A-Z0-9.\-]{0,9}$`. Bioguide resolved once
  per PTR using the first trade's date for term-overlap. With
  `--apply`, Tier 1 + Tier 3 rows upsert. Tiers 2 + 4 are CSV-logged
  for follow-up rescue / review.
- **Outcome**: 8,155 PTRs → 53,081 rows. Bioguide resolution ~94%.
  Header-pollution detection eliminated ~400+ contaminated rows.
- **Status**: One-shot complete (executed 2026-05-01). Historical
  window closed; daily ingest now keeps current year fresh.
- **Where the logic lives now**: Tier classification, paper-scan
  heuristic, and `_paper_placeholder_row()` are in
  `src/factorlab/sources/political/house_clerk/ingest.py`.
  PDF parser is `house_clerk/parser.py`. The asset classifier and
  bioguide matcher are package-shared at
  `political/_asset_classifier.py` and `political/_bioguide.py`.

### 2. House paper-PTR placeholders

- **Script**: `scripts/archive/backfill_house_paper_ptr_placeholders.py`
- **Purpose**: Walk House Clerk PTR PDFs on disk, detect paper scans,
  insert placeholder rows for OCR-pending filings.
- **Motivation**: Earlier ad-hoc scrapes of House Clerk PDFs predated
  the placeholder-insertion logic. Audit trail had a gap: nothing
  recorded which filings were paper-scanned and how many pages each
  had. Without this, downstream OCR tracking has no inventory to work
  from.
- **Approach**: Uses `pdfplumber` to extract text from each PDF; same
  paper-scan heuristic as the historical load (validated zero FPs on
  1,869 PDFs). Bioguide resolved once per PTR. Builds row with
  `asset_name_raw = "[PAPER PTR — N pages — OCR pending]"` so OCR'd
  trade rows can land beside it without UNIQUE conflict.
- **Outcome**: Placeholder rows for 2013-2026 House paper PTRs.
- **Status**: One-shot complete. Idempotent (safe to re-run).
- **Where the logic lives now**: `house_clerk/ingest.py:_paper_placeholder_row()`
  + main ingest loop calls it on every paper-detected PDF.

### 3. Senate paper-PTR placeholders

- **Script**: `scripts/archive/backfill_paper_ptr_placeholders.py`
- **Purpose**: Backfill Senate paper-PTR placeholder rows from the
  cached `*_paper.json` sidecars that the eFD scraper writes alongside
  page-image GIFs.
- **Motivation**: Same audit-trail gap as House paper PTRs but on the
  Senate side. The eFD scraper writes JSON sidecars with
  `filing_url`, `page_count`, and `page_image_urls`; pre-existing
  scrapes lacked the corresponding placeholder rows in DB.
- **Approach**: Walks `data/political/raw/senate_efd/ptrs/*_paper.json`.
  Filename pattern: `MM-DD-YYYY_LAST_FIRST_uuid_paper.json`. Reads
  sidecar JSON for filing_url and page_count, reconstructs the
  manifest dict that `_paper_placeholder_row()` expects, resolves
  bioguide via `StrictBioguideMatcher`. Defensive intra-batch dedup
  before `fast_upsert`.
- **Outcome**: Placeholder rows for Senate paper PTRs (2020-2026
  window). Page counts and bioguide resolution captured per filing.
- **Status**: One-shot complete. Idempotent.
- **Where the logic lives now**: `senate_efd/ingest.py:_paper_placeholder_row()`
  is the shared template; `senate_efd/scraper.py:backfill_paper_gifs_from_sidecars()`
  is the in-pipeline equivalent now called automatically at scrape time.

### 4. Senate eFD cache reparse (NULL transaction_type fix)

- **Script**: `scripts/archive/_reparse_senate_efd_cache.py`
- **Purpose**: Rebuild eFD rows whose `transaction_type` landed as NULL
  due to a parser header-lookup bug. Uses the local HTML cache so no
  Playwright / Akamai re-scrape is needed.
- **Motivation**: The parser used `col("type")` as a substring match
  against header rows. Senate eFD HTML places "Asset Type" as a column
  header alongside "Type" — substring matching grabbed "Asset Type"
  first, causing ~30-40% of 2024-2026 rows to land with NULL
  `transaction_type`. The fix (exact-match-first lookup) shipped to
  `senate_efd/parser.py`. The DB still held the bad rows, and
  re-scraping was prohibitive (Akamai blocks; residential IP only).
- **Approach**:
  1. Query DB for filings with ANY NULL `transaction_type` (excluding
     paper-placeholder rows).
  2. Locate the cached HTML by extracting the UUID from the filename
     (filenames embed the UUID with underscores instead of dashes).
  3. **DELETE** existing rows for that filing (the unique constraint
     would block an upsert from NULL to a real value).
  4. Re-parse HTML with the fixed parser.
  5. UPSERT corrected rows (transaction_type now populated).
- **Outcome**: ~30-40% of 2024-2026 rows recovered. NULL
  `transaction_type` count dropped to near-zero post-run.
- **Status**: One-shot, crash-recovery. Won't run again unless the
  parser regresses.
- **Where the logic lives now**: Parser fix in
  `senate_efd/parser.py` (exact-match-first column lookup).

### 5. House Clerk Tier-2 rescue

- **Script**: `scripts/archive/rescue_house_clerk_tier2.py`
- **Purpose**: Salvage Tier 2 rows (electronic + trades but quality
  gate failed) that domain review verified were real trades.
- **Motivation**: The historical load's Tier 2 CSV held ~N rows
  flagged for `bioguide_unresolved`, `header_pollution`, or
  `asset_too_short`. Manual review showed many were fixable: some
  unresolved bioguides resolved via aliases (Van Taylor → T000479
  motivated migration 024 seeding `legislator_aliases`); some
  contaminated asset names (`"broker: XYZ"`, `"purchased shares"`,
  `"death"`) had a clean ticker that could replace the polluted name
  per strict-NULL.
- **Approach**: Reads `tier2_review.csv`. For each row: (a) check
  `legislator_aliases` for a manual override; (b) validate ticker
  (non-null, < 10 chars, no spaces); (c) clean `asset_name_raw` if it
  contains pollution markers by falling back to ticker; (d) build
  rescue row with resolved bioguide + cleaned name + ticker; (e)
  skip if both bioguide and ticker fail. Stats counter tracks
  `bioguide_overridden`, `skipped_no_bioguide`, `skipped_no_ticker`,
  `rescued`.
- **Outcome**: Rescued N rows; skipped M for failing both gates.
- **Status**: One-off batch. Won't be re-run — `legislator_aliases`
  is now Stage 0 of the matcher, so future Tier 2 reviews never need
  this exact script again. New manual overrides are SQL inserts (see
  [political-pipeline.md](political-pipeline.md#adding-a-manual-bioguide-override)).
- **Where the logic lives now**: Alias lookup is Stage 0 of
  `_bioguide.StrictBioguideMatcher.resolve()`. Polluted-asset detection
  + ticker fallback was domain-specific to this batch and is **not**
  upstreamed — the modern ingest's quality gates reject these rows
  rather than rescue them.

### 6. Asset-resolution backfill — `[KEPT, RECURRING]`

- **Script**: `scripts/backfill_asset_resolution.py`
- **Purpose**: Batch re-resolve `ticker` + `asset_type_code` for any
  `legislator_trades` rows where either field is NULL.
- **Motivation**: Early ingest runs and manual uploads sometimes
  produced rows with missing classifier output. The `AssetClassifier`
  improves over time (new alias rules, new asset patterns); this
  script applies the current classifier to NULL-field rows without
  re-fetching source documents.
- **Approach**: Scan all rows where `ticker IS NULL OR asset_type_code
  IS NULL`. Run `AssetClassifier.classify(asset_name_raw)`. Dry-run
  (default) reports proposed updates by reason + top-15 newly-attached
  tickers + asset-code distribution. `--apply` batches UPDATE by
  `trade_id` (1000 rows / transaction). Non-destructive: only sets
  NULL fields, never overwrites a populated value.
- **Status**: Recurring utility. Run after classifier improvements or
  as periodic confidence refresh.

### 7. Bioguide refresh — `[KEPT, RECURRING]`

- **Script**: `scripts/refresh_bioguide.py`
- **Purpose**: Re-apply `StrictBioguideMatcher` to existing rows
  without re-fetching or re-parsing source documents.
- **Motivation**: Matcher rules evolve — new aliases, edge-case
  handling, term-boundary fixes. Re-ingesting from source is
  expensive; recomputing in-place captures improvements
  retroactively.
- **Approach**: Scan rows (optionally filtered by `--source`),
  re-resolve bioguide via `matcher.resolve(full_name, chamber,
  trade_date)`, log transitions
  (`null→resolved`, `resolved→null`, `bioguide_changed`,
  `unchanged`). Dry-run reports without writing; `--apply` updates
  in batches.
- **Status**: Recurring utility. Run after matcher updates.

### 8. Paper-PTR image download (Senate eFD)

- **Script**: `scripts/archive/download_paper_ptr_images.py`
- **Purpose**: Download page-image GIFs referenced by Senate eFD paper-PTR
  JSON sidecars into `data/political/raw/senate_efd/paper_images/{filing_uuid}/p{N}.gif`.
- **Motivation**: Paper PTRs are scanned filings; `page_image_urls`
  in the JSON sidecar points to GIFs on the eFD server. Downstream
  OCR / vision-LLM extraction needs the images cached locally.
- **Approach**: Walk `*_paper.json` sidecars. For each
  `page_image_urls` entry, download via HTTP (idempotent — skip if
  file exists). Per-filing status: OK (all pages downloaded) or
  PARTIAL (some failed/missing). User-Agent from
  `FACTORLAB_USER_AGENT` env var.
- **Outcome**: Local GIF cache for 2020-2026 Senate paper PTRs.
- **Status**: One-time historical download.
- **Where the logic lives now**: Backfill behavior is now in
  `senate_efd/scraper.py:backfill_paper_gifs_from_sidecars()`,
  called automatically at the start of every eFD scrape run. The
  standalone script is no longer needed.

### 9. Senate eFD vision-LLM extraction insert — `[KEPT, RECURRING]`

- **Script**: `scripts/_insert_senate_efd_llm_extractions.py`
- **Purpose**: Insert vision-LLM-extracted Senate paper-PTR trades
  from manual review JSON files into `legislator_trades` under the
  distinct `senate_efd_paper_llm` endpoint.
- **Motivation**: Paper-filed PTRs (GIF scans) require OCR. Claude
  vision was used to manually extract trades from sampled GIFs;
  results landed in
  `data/political/raw/senate_efd/_llm_extractions/{filing_id}.json`.
  This script ingests them under a distinct endpoint code so the
  source is provenance-tracked.
- **Approach**: Walk `_llm_extractions/*.json`. Each JSON has trades
  with `asset_name_raw`, `tx_date`, `amount_str/min/max/mid`,
  `ticker_guess`, `section ∈ {sales, purchases, exchanges}`. Map
  section → transaction_type
  (`sales` → `sale_full`, `purchases` → `purchase`, `exchanges` →
  `exchange`). Resolve ticker: prefer LLM guess, fall back to
  `AssetClassifier`. Skip rows whose section is unknown
  (strict-NULL). Build row with
  `endpoint_id = senate_efd_paper_llm`. Defensive intra-batch dedup
  before `fast_upsert`.
- **Status**: Recurring. Vision-LLM extraction is an ongoing manual
  pipeline; new extraction JSONs land periodically and this script
  ingests each batch.

### 10. FEC corrupt-cache cleaner — `[KEPT, RECURRING]`

- **Script**: `scripts/_clean_corrupt_fec_cache.py`
- **Purpose**: Detect and delete truncated/corrupt JSON files in the
  FEC cache after a scraper crash, so the next backfill run isn't
  blocked replaying bad cache hits.
- **Motivation**: When the FEC scraper crashes mid-download, files
  receive partial writes. `json.loads` chokes on the partial bytes
  with codec errors. Re-running the backfill replays these as cache
  hits and fails the same way until the bad files are removed.
- **Approach**: Multiprocessing pool (cpu_count − 1) — single-process
  scan over ~33K files takes ~10 min; pooled <2 min. Each worker
  attempts `json.loads(path.read_bytes())`, returns
  `(path, error_msg or None)`. Dry-run lists corrupt paths + first 80
  chars of error. `--delete` unlinks them. Handles `OSError` (locked
  / missing files) gracefully.
- **Status**: Recurring. Run after a crash or prophylactically before
  heavy scraping. Domain-specific to FEC cache health — not
  upstreamed.

---

## Status summary

| Script | Type | Post-Tier-1 location | Logic now lives in |
|---|---|---|---|
| load_house_clerk_historical.py | one-shot | `scripts/archive/` | `political/house_clerk/ingest.py` + `parser.py` |
| backfill_house_paper_ptr_placeholders.py | one-shot | `scripts/archive/` | `political/house_clerk/ingest.py:_paper_placeholder_row()` |
| backfill_paper_ptr_placeholders.py | one-shot | `scripts/archive/` | `political/senate_efd/ingest.py:_paper_placeholder_row()` |
| _reparse_senate_efd_cache.py | one-shot crash-recovery | `scripts/archive/` | parser fix in `political/senate_efd/parser.py` |
| rescue_house_clerk_tier2.py | one-off batch | `scripts/archive/` | aliases in `political/_bioguide.py` (Stage 0) |
| download_paper_ptr_images.py | one-time | `scripts/archive/` | `political/senate_efd/scraper.py:backfill_paper_gifs_from_sidecars()` |
| backfill_asset_resolution.py | recurring utility | `scripts/` | uses `political/_asset_classifier.py` |
| refresh_bioguide.py | recurring utility | `scripts/` | uses `political/_bioguide.py` |
| _insert_senate_efd_llm_extractions.py | recurring utility | `scripts/` | domain-specific; not upstreamed |
| _clean_corrupt_fec_cache.py | recurring utility | `scripts/` | domain-specific; not upstreamed |

---

## When you might revive an archived script

You don't, in general — the upstream code in `src/` covers every recurring
case. Edge cases where you'd un-archive:

- **Parser regression** that re-introduces NULL `transaction_type` rows
  → un-archive `_reparse_senate_efd_cache.py` after fixing the parser.
- **Bulk re-classification** (e.g. `legislator_aliases` adds 50 new
  overrides at once) where `refresh_bioguide.py` isn't sufficient
  → start from `rescue_house_clerk_tier2.py` as a template.
- **Schema migration** that changes the conflict key → the historical
  load patterns may need to re-run; start from `load_house_clerk_historical.py`.

When un-archiving, **do not** copy back into `scripts/` — copy out as a
new file under `scripts/<new_name>.py`. Archived scripts are a frozen
record; in-place edits would erode their value.
