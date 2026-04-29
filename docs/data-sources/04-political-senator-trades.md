# Political Signals — US Congressional Trades

> Status: `[building, free-tier-only]` · Last updated: 2026-04-29 · Verified empirically end-to-end against live sources

## Purpose

US legislators are required to file equity transactions under the **STOCK Act of 2012** via Periodic Transaction Reports (PTRs). The alpha thesis is not in following large-cap trades (AAPL, NVDA — crowded, no edge) but in **small/micro-cap names traded by members on relevant oversight committees**, cross-referenced with government contracts and lobbying data.

The binding constraint: filings have a **45-day reporting window**, so by the time data is public, the transaction is 1–45 days old. The `filing_date` IS the signal date. But the *structural backing* signal (member keeps buying, company keeps winning contracts) is durable across 90–180 days post-disclosure.

---

## Persistent storage — `alt_political` schema (LIVE 2026-04-30)

Schema lives in Postgres `alt_political.*` (26 tables). See [docs/architecture/02-database-postgres.md](../architecture/02-database-postgres.md) for full ER + index map. Migrations: [007_create_alt_political_tables.py](../../migrations/versions/007_create_alt_political_tables.py) + [008_seed_alt_political_reference.py](../../migrations/versions/008_seed_alt_political_reference.py).

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

**Country tagging**: every dim and event row carries `country_code` FK to `ref.countries.code`, defaulting to `'US'`. Multi-jurisdiction expansion (UK MP register, EU MEPs, India parliamentary disclosures) reuses the same tables — different `country_code`, same shape. See [docs/architecture/04-multi-country-schema.md](../architecture/04-multi-country-schema.md) for the country-tagging convention.

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
FROM alt_political.legislator_trades t
JOIN alt_political.legislators l USING (bioguide_id)
JOIN alt_political.committee_assignments ca USING (bioguide_id)
JOIN alt_political.committee_sector_map csm
  ON ca.country_code = csm.country_code AND ca.committee_id = csm.committee_id
LEFT JOIN alt_political.gov_contracts gc
  ON gc.ticker = t.ticker
  AND gc.action_date BETWEEN t.transaction_date - INTERVAL '90 days'
                         AND t.transaction_date + INTERVAL '90 days'
LEFT JOIN alt_political.lobbying_filings lf
  ON lf.client_ticker = t.ticker
  AND lf.filing_year = EXTRACT(YEAR FROM t.transaction_date)
WHERE t.transaction_type = 'purchase'
  AND csm.signal_strength IN ('extreme','very_high','high')
  AND ca.valid_to IS NULL  -- currently serving on the committee
ORDER BY t.filing_date DESC;
```

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

### 1B. Senate eFD (PRIMARY for Senate, free, must build)

| Field | Detail |
|-------|--------|
| URL | https://efdsearch.senate.gov |
| Auth | No auth, but requires accepting an agreement page (cookie-based session) |
| Format | HTML search → individual PDF filings |
| Volume | 100 senators |
| Difficulty | Higher — PDFs, agreement page, no bulk XML |

**⚠ Earlier "v1 shortcut to Senate Stock Watcher" recommendation is dead** (see §2A). Direct eFD scraping is now the only free path to live Senate trades.

**Agreement page is the #1 scraping failure point.** Session cookie expires on inactivity. Scraper must detect redirect back to `/search/home/` and re-POST the agreement form automatically.

**Filing types:** Periodic Transaction Reports (PTRs), Annual Financial Disclosures, Amendments. Filter to PTRs for trades.

**PDF structure:**
- Post-2020: mostly text-extractable structured PDFs → `pdfplumber` works well
- 2012–2020: mixed quality, some scanned → OCR fallback (`pytesseract` or PyMuPDF + Tesseract)
- Pre-2012: no electronic filing (STOCK Act signed April 2012)

**Build approach (planned — see `playground/explore/senate_efd/`):**
1. POST agreement form → capture session cookie
2. Search PTRs by date window via `/search/report/` (returns HTML table)
3. For each result row: fetch individual PTR PDF, parse with same pdfplumber pipeline as House
4. Schema parity with House: owner, ticker, asset_name_raw, asset_type, tx_type, tx_date, notif_date, amount_min/max/mid

**Backfill split:**
- 2014-2019: Senate Stock Watcher GitHub mirror (8,350 rows, frozen 2021) — see §2A
- 2020-2026: must scrape eFD directly

### 1C. Congress.gov API (committee data + member metadata)

| Field | Detail |
|-------|--------|
| URL | https://api.congress.gov/v3/ |
| Auth | Free API key (sign up at api.congress.gov/sign-up/, instant) |
| Rate limit | 5,000 requests/hour per key |
| Format | JSON (default) or XML |

**Key endpoints:**
```
GET /v3/member                              # All members (current + historical)
GET /v3/member/{bioguideId}                 # Single member detail
GET /v3/member/{bioguideId}/committees      # Member's committee assignments
GET /v3/committee/{chamber}/{committeeCode} # Committee membership lists
```

**Bioguide ID** is the canonical cross-reference identifier. Other IDs: Thomas ID, GovTrack ID, OpenSecrets CID, FEC ID. Full crosswalk available in `unitedstates/congress-legislators` YAML files.

**Limitation:** Current committee assignments only. For **historical** committee assignments (critical for point-in-time analysis), use:
- `github.com/unitedstates/congress-legislators` → `committee-membership-current.yaml` + git history snapshots
- This is a **genuinely open research gap** — no provider solves historical point-in-time committee membership well

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

---

## Part 2 — Aggregator Providers (Pros/Cons)

### 2A. Senate Stock Watcher (DEAD — historical 2014-2019 only)

> **Verified dead 2026-04-29.** S3 buckets return HTTP 403 across all known regions/paths. `senatestockwatcher.com` DNS does not resolve. GitHub data mirror frozen with last commit 2021-03-16; transaction data ends 2019-12-31. Upstream creator moved to AnythingLLM. **Do NOT use as a live source.**

The `senate-stock-watcher-data` GitHub mirror is still accessible at `raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/master/aggregate/all_transactions.json` and gives **8,350 rows covering 2014-01-01 → 2019-12-31** — useful only for historical backfill of currently-serving senators.

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

### `alt_political.legislator_trades`
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

### `alt_political.legislator_committees`
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

### `alt_political.legislators`
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

### `alt_political.committee_sector_map`
```sql
committee_code     text NOT NULL              -- Thomas ID
gics_code          text NOT NULL              -- GICS industry code
signal_strength    text                       -- 'extreme', 'very_high', 'high', 'medium', 'low'
rationale          text
PRIMARY KEY (committee_code, gics_code)
```

### `alt_political.gov_contracts`
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

### `alt_political.lobbying`
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

**Signal note:** Amount relative to net worth matters. A $15K-$50K buy from a senator worth $500K = conviction. Same range from one worth $50M = noise. Join against `alt_political.legislators.net_worth_*`.

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
│   → INSERT alt_political.legislator_trades                       │
│                                                                  │
│ Senate 2014-2019 (frozen):                                       │
│   Pull GitHub mirror's all_transactions.json (8,350 rows)        │
│   → field-rename + synthesize filing_date = txn + 30d            │
│   → INSERT alt_political.legislator_trades                       │
│                                                                  │
│ Senate 2020-today:                                               │
│   Direct eFD scraper: agreement-form POST → cookie session       │
│   → /search/report PTR list by date window                       │
│   → for each PTR, fetch + parse PDF                              │
│   → INSERT alt_political.legislator_trades                       │
│                                                                  │
│ Members + committees:                                            │
│   GET raw legislators-current.yaml + committees-current.yaml     │
│       + committee-membership-current.yaml                        │
│   → INSERT alt_political.legislators / .legislator_committees    │
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
│   → INSERT alt_political.gov_contracts                           │
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
FROM alt_political.legislator_trades t
JOIN alt_political.legislator_committees c
  ON t.legislator_id = c.legislator_id
  AND t.transaction_date BETWEEN c.valid_from AND COALESCE(c.valid_to, '9999-12-31')
JOIN alt_political.committee_sector_map csm
  ON c.committee_code = csm.committee_code
JOIN ref.securities s
  ON t.security_id = s.security_id
  AND s.gics_code LIKE csm.gics_code || '%'
LEFT JOIN alt_political.gov_contracts g
  ON t.ticker = g.ticker
  AND g.award_date BETWEEN t.transaction_date - INTERVAL '180 days'
                       AND t.transaction_date + INTERVAL '90 days'
LEFT JOIN alt_political.lobbying l
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
- [x] **Extend to lobbying and government contracts?** → Yes, in `alt_political` schema. Critical for the small-cap thesis. Phase 2 via Quiver or Finnhub free tier.

## Open Questions

- [ ] Historical committee assignments: build from git history of `unitedstates/congress-legislators`? This is an open research gap.
- [ ] Comparable EU (MEPs) and India (parliamentary) disclosures — are they public? Phase 6+ scope.
- [ ] PEP scope creep — governors, federal judges, fed governors? Not in v1.
- [ ] Campaign finance cross-reference: "Company X donated to Senator Y" + "Senator Y bought Company X stock" — via OpenSecrets bulk data?
