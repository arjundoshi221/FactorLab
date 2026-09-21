# SEC EDGAR — Filings, XBRL, Full-Text

> Status: `[api-surface]` — HTTP client + endpoint modules landed 2026-09-21. Real-sample catalog under [`data/edgar/samples/CATALOG.md`](../../data/edgar/samples/CATALOG.md). Per-form parsers and schema wiring pending post-migration.

## Purpose

Canonical, free, machine-readable source for every US securities filing. One API surface unlocks, in a single integration:

- **Fund holdings** (N-PORT) — look-through into every US ETF/mutual fund
- **Institutional positioning** (13F-HR) — every manager >$100M
- **Insider trades** (Form 3/4/5) — T+1 disclosure of every officer/director trade
- **Fund launch pipeline** (N-1A / 497K) — 60–90 day lead on new ETFs
- **Beneficial ownership** (SC 13D / SC 13G) — 5%+ activist / passive stakes
- **Material events** (8-K) — real-time corporate disclosure
- **Fund reference** (N-CEN) — adviser / custodian / auditor / fee waivers
- **Proxy / governance** (DEF 14A) — comp, board, activist proposals
- **IPO pipeline** (S-1 / 424B) — pre-IPO financials
- **Fundamentals** (XBRL in 10-K/10-Q) — every tagged line item

Every commercial vendor (Morningstar, FactSet, WhaleWisdom, ETF Global) is a wrapper over EDGAR. Owning the source unlocks all of these with one integration and $0 in vendor fees.

## Source

Three hosts, one API surface:

| Host | Purpose |
|------|---------|
| `www.sec.gov` | Archives (raw filings), full-index (daily/quarterly), RSS feeds |
| `data.sec.gov` | Structured JSON — submissions, XBRL companyfacts, frames |
| `efts.sec.gov` | Full-text search across every filing since 2001 |

## Pricing

**$0.** No auth beyond a self-identifying User-Agent header. No quotas beyond rate limit.

## Auth

SEC requires **every request** to carry a `User-Agent` header that identifies the caller. Requests without one, or with a fake browser UA, are 403'd.

- Format (per [SEC Fair Access policy](https://www.sec.gov/os/accessing-edgar-data)): `Sample Company Name AdminContact@samplecompany.com`
- Loaded from `EDGAR_USER_AGENT` env var — client raises `OSError` if unset
- Never hardcoded — the "no personal email in code" rule has an API-contract carve-out that applies here

Client also sends `Accept-Encoding: gzip, deflate` and a host header per request (`www.sec.gov` / `data.sec.gov`).

## Rate limits

| Limit | Value | Scope |
|-------|-------|-------|
| Requests per second | **10** | Per IP, all hosts combined |
| Burst tolerance | Modest | 429s appear if you sustain 10 rps without jitter |
| Daily calls | None | No documented daily cap |

Adapter uses a 100 ms floor between requests (`MIN_REQUEST_INTERVAL = 0.1`), 5 retries with exponential backoff, honors `Retry-After` on 429/5xx.

**Warning:** the 10 rps cap is per-IP, not per-key (no keys exist). If both VPS and local dev hammer EDGAR concurrently they share the budget — coordinate.

## Endpoints in use

### Archives (`www.sec.gov`)

| Path | Returns |
|------|---------|
| `/Archives/edgar/full-index/{YYYY}/QTR{n}/form.idx` | Quarterly index sorted by form type |
| `/Archives/edgar/full-index/{YYYY}/QTR{n}/master.idx` | Quarterly index sorted by CIK |
| `/Archives/edgar/daily-index/{YYYY}/QTR{n}/form.{YYYYMMDD}.idx` | Per-day filings by form (weekdays only) |
| `/Archives/edgar/data/{cik_int}/{accession_no_dashes}/` | Filing document folder (HTML directory listing) |
| `/Archives/edgar/data/{cik_int}/{accession_no_dashes}/index.json` | Filing document listing as JSON — always start here |
| `/cgi-bin/browse-edgar?action=getcompany&type={form}&output=atom` | Atom feed per form (real-time-ish) |

### Structured JSON (`data.sec.gov`)

| Path | Returns |
|------|---------|
| `/submissions/CIK{cik:010d}.json` | Every filing by a filer (last 1,000 inline + paginated) |
| `/api/xbrl/companyfacts/CIK{cik:010d}.json` | Every XBRL fact ever reported by that filer |
| `/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{tag}.json` | One concept (e.g. Revenues) across all filings by one filer |
| `/api/xbrl/frames/{taxonomy}/{tag}/{unit}/CY{YYYY}Q{n}I.json` | One concept across every filer for one period |

### Reference

| Path | Returns |
|------|---------|
| `https://www.sec.gov/files/company_tickers.json` | Ticker ↔ CIK ↔ company name (all filers) |
| `https://www.sec.gov/files/company_tickers_exchange.json` | Same + listing exchange |

### Full-text search (`efts.sec.gov`)

| Path | Returns |
|------|---------|
| `/LATEST/search-index?q=&forms=&dateRange=custom&startdt=&enddt=&ciks=` | JSON hits with accession, form, filer, snippet |

---

## Filing lifecycle & identifiers

Every filing on EDGAR is uniquely identified by an **accession number** — a 20-character string of the form `NNNNNNNNNN-YY-NNNNNN` (e.g., `0000320193-26-000018`). The first 10 digits are the filing agent's CIK, the middle 2 are the year, the last 6 are a per-year sequence.

Filed by a **CIK** (Central Index Key) — an integer up to 10 digits. CIKs are immutable per filer; tickers change but CIKs don't. In URLs, CIKs are zero-padded to 10 digits; in JSON responses they're unpadded ints.

**Amendments** are new filings with a form suffix: `10-K/A`, `4/A`, `N-1A/A`, etc. The original stays in EDGAR; the amendment adds a new accession that supersedes it. Downstream storage must key on (form, accession) and let the newest filing win when reasoning about "current."

**Series / Class IDs** (fund-specific): a single N-1A / 497K / N-CEN may register multiple **series** (each series is a distinct fund) and each series may have multiple **share classes**. IDs look like `S000010041` and `C000027790`. When storing fund reference data, series_id is the correct join key — not accession, not CIK.

---

## Filing archive structure

Every filing lives under `/Archives/edgar/data/{cik_int}/{accession_no_dashes}/`. Contents follow a predictable pattern. Empirical breakdown (from the [samples catalog](../../data/edgar/samples/CATALOG.md)):

| File | What it is | Pull? |
|------|-----------|-------|
| `{accession}-index.html` | Human navigation page for the filing | **Skip** — SEC-generated |
| `{accession}-index-headers.html` | SEC's internal header dump | **Skip** — SEC-generated |
| `{accession}.txt` | Full SGML-wrapped submission (all docs concatenated) | Skip unless you need the SGML wrapper's `<TYPE>` metadata |
| `primary_doc.xml` | The structured XML payload for modern forms (N-PORT, N-CEN, 13F cover) | **Always pull** |
| `{formname}.xml` / `{numeric}.xml` | Form-specific structured payload (Form 4 uses `form4.xml`, `wf-form4_*.xml`; 13F info tables have names like `56757.xml`) | **Always pull** |
| `{ticker}-{date}.htm` | Main HTML body for 10-K/10-Q/DEF 14A/S-1/N-1A (the biggest HTML file) | Pull for narrative content |
| `{ticker}-{date}_htm.xml` | Inline XBRL instance (the HTML body re-tagged) | Skip — prefer `data.sec.gov` companyfacts |
| `{ticker}-{date}_cal/def/lab/pre.xml` | XBRL linkbases (calculation / definition / label / presentation) | Skip — only needed if parsing XBRL from scratch |
| `R{n}.htm` | XBRL Viewer's rendered fact page (one per statement) | **Skip** — noise |
| `FilingSummary.xml` | Manifest of the above | Skip — you already have `index.json` |
| `Financial_Report.xlsx` | Auto-generated Excel of the XBRL facts | Skip — same data via API |
| `MetaLinks.json` | XBRL taxonomy metadata | Skip |
| `img*.jpg`, `*.gif` | Embedded images (logos, charts) | Skip |
| `ex-*.htm`, `exhibit*.htm` | Exhibits (press releases in 8-K, underwriting agreements in S-1, etc.) | Pull selectively — the 8-K "primary" is often `ex99*.htm` (the press release) |

**Rule of thumb:** call `index.json` first, download all `.xml` files that aren't XBRL linkbases (`_cal/_def/_lab/_pre/_htm.xml`), plus the largest `.htm` that isn't a nav file or `R{n}.htm`.

---

## Format families

EDGAR is a decade of accreted formats. Here's the full menu with examples pulled from real samples.

### 1. `.idx` — the catalog format

Pipe-delimited fixed-width text. Preamble → wrapped header → `----` divider → data rows separated by 2+ whitespace. Column order differs between `form.idx` (Form, Company, CIK, Date, File) and `master.idx` (CIK, Company, Form, Date, File). Date is `YYYYMMDD` (no dashes).

Parsed by [`sources/edgar/filings.py::parse_idx`](../../src/factorlab/sources/edgar/filings.py). Handles both orderings.

### 2. `{accession}.txt` — SGML submission wrapper

Original EDGAR upload format. Contains all documents in a single file bracketed by `<DOCUMENT>` blocks with `<TYPE>`, `<SEQUENCE>`, `<FILENAME>`, `<DESCRIPTION>`, `<TEXT>`. Useful when you want the SEC-declared document type without walking `index.json`.

Modern filings publish each document as a separate file too, so parsing SGML is rarely necessary.

### 3. `primary_doc.xml` — structured form payload

Post-2003 fund forms (N-PORT, N-CEN, 13F cover) and modern Form 3/4/5 filings ship a structured XML root document. Root element varies by form: `<edgarSubmission>`, `<ownershipDocument>`, `<informationTable>`. Namespace-free, ~human-readable.

**This is your primary parse target for structured data.** ElementTree / lxml handle it directly.

### 4. Form-specific XML sidecars

Some forms use non-primary XML filenames:
- **Form 4/3/5**: `form4.xml`, `a3.xml`, `doc5.xml`, `wf-form4_*.xml`, `ownership.xml` — root is `<ownershipDocument>`
- **13F-HR**: `primary_doc.xml` is the cover; the holdings live in `form13fInfoTable.xml` (or an arbitrarily-named file like `56757.xml`) with root `<informationTable>`

Always download every `.xml` in the filing that isn't an XBRL linkbase.

### 5. HTML narrative

Traditional prospectus/proxy/registration content: N-1A, 497K, DEF 14A, S-1, 10-K/10-Q body, SC 13D/G. Free-text with tables. Parse with BeautifulSoup + regex heuristics; ML entity extraction is optional if precision matters.

### 6. Inline XBRL (iXBRL)

10-K / 10-Q / 8-K / DEF 14A published since ~2019 embed XBRL tags directly in the HTML body via `<ix:nonFraction>`, `<ix:nonNumeric>`, `<ix:hidden>`. The same filing may ship:
- The HTML body (with inline tags) — `{ticker}-{date}.htm`
- A standalone XBRL instance — `{ticker}-{date}_htm.xml`
- Four linkbases — `_cal / _def / _lab / _pre.xml`

**Don't parse XBRL from scratch.** SEC's `data.sec.gov` XBRL API returns the same facts pre-parsed as JSON. Use that path unless a specific tag / context is missing.

### 7. JSON (`data.sec.gov`)

- **Submissions** — every filing by a filer, as parallel arrays under `filings.recent` plus paginated older-history files. Ready for our `submissions.py`.
- **companyfacts** — every XBRL fact ever reported. Shape: `{"cik": ..., "entityName": ..., "facts": {"us-gaap": {"Revenues": {"units": {"USD": [ {val, start, end, accn, form, filed, frame} ]}}}}}`. AAPL has 503 us-gaap tags.
- **companyconcept** — one tag over time for one filer. Same shape as one entry in companyfacts.
- **frames** — one tag across every filer for one period. AAPL's `Assets` for CY2024Q4: 6,267 filers.

---

## Forms — what's in each

Every entry below points to a real filing in [`data/edgar/samples/`](../../data/edgar/samples/) so you can eyeball the raw content.

### Form 4 — insider transaction (P1)

- **Cadence**: T+1 after any officer / director / 10% owner trade
- **Format**: XML with root `<ownershipDocument>`
- **Filename**: `form4.xml`, `wf-form4_*.xml`, or `ownership.xml`
- **Schema versions in the wild**: X0202 through X0609 (2003 → 2026). Parsers must handle all.

Sample: [Apple Inc, Jennifer Newstead sale of AAPL on 2026-09-15](../../data/edgar/samples/4/0001140361-26-037020/form4.xml)

Key elements:

| XPath | Sample value | Notes |
|-------|--------------|-------|
| `/ownershipDocument/schemaVersion` | `X0609` | Format version |
| `/ownershipDocument/documentType` | `4` | Also 3, 5, or {form}/A for amendments |
| `/ownershipDocument/periodOfReport` | `2026-09-15` | Transaction date basis |
| `/issuer/issuerCik` | `0000320193` | Zero-padded |
| `/issuer/issuerName` | `Apple Inc.` | |
| `/issuer/issuerTradingSymbol` | `AAPL` | |
| `/reportingOwner/reportingOwnerId/rptOwnerCik` | `0001780525` | |
| `/reportingOwner/reportingOwnerId/rptOwnerName` | `Newstead Jennifer` | |
| `/reportingOwner/reportingOwnerRelationship/isDirector` | `0` or `1` | |
| `/reportingOwner/reportingOwnerRelationship/isOfficer` | `1` | |
| `/reportingOwner/reportingOwnerRelationship/isTenPercentOwner` | `0` | |
| `/reportingOwner/reportingOwnerRelationship/officerTitle` | `SVP, GC and Government Affairs` | |
| `/aff10b5One` | `true` | Rule 10b5-1 plan flag — matters for signal quality |
| `/nonDerivativeTable/nonDerivativeTransaction/securityTitle/value` | `Common Stock` | |
| `.../transactionCoding/transactionCode` | `S` | See transaction-code table below |
| `.../transactionAmounts/transactionShares/value` | `42686` | Wrapped in `<value>` |
| `.../transactionAmounts/transactionPricePerShare/value` | `234.56` | |
| `.../transactionAmounts/transactionAcquiredDisposedCode/value` | `A` or `D` | |
| `.../postTransactionAmounts/sharesOwnedFollowingTransaction/value` | `102345` | |
| `.../ownershipNature/directOrIndirectOwnership/value` | `D` or `I` | |
| `/derivativeTable/derivativeTransaction/...` | Options/warrants (parallel structure) | |
| `/footnotes/footnote` | `This transaction was made pursuant to a Rule 10b5-1 trading plan...` | |

**Transaction codes** (partial):

| Code | Meaning |
|------|---------|
| P | Open-market purchase |
| S | Open-market sale |
| A | Grant / award |
| M | Exercise / conversion of derivative |
| F | Payment of exercise price or tax |
| G | Bona fide gift |
| C | Conversion of derivative |
| D | Disposition (multi-purpose) |
| I | Discretionary transaction |
| J | Other |
| X | Exercise of in-/out-of-the-money derivative |
| K | Equity-swap transaction |
| L | Small acquisition (under Rule 16a-6) |
| V | Voluntary early reporting |

**Signal quality**: Rule 10b5-1 pre-planned sales (`aff10b5One = true`) are noise. Open-market purchases (`P`) with no 10b5-1 flag are the highest-conviction signal.

### Form 3 — initial insider filing (P1)

Filed once, when someone first becomes an insider. Reports current holdings (no transactions).

Sample: [QuestMark Partners LP, initial filing for eHealth Inc (EHTH) 2006-10-12](../../data/edgar/samples/3/0001104659-06-066373/a3.xml)

Same schema shape as Form 4 but:
- `documentType` = `3`
- `noSecuritiesOwned` = `0` or `1` (if 1, filer holds nothing)
- No transaction tables — just `<nonDerivativeHolding>` / `<derivativeHolding>` reporting position

### Form 5 — annual insider summary (P1)

Filed within 45 days of fiscal year end. Captures anything a filer missed during the year (small gifts, exempt transactions).

Sample: [Urban Outfitters, Frank Conforti (CFO) 2015-03-17](../../data/edgar/samples/5/0001209191-15-027198/doc5.xml)

Same schema as Form 4 but:
- `documentType` = `5`
- `form3HoldingsReported` / `form4TransactionsReported` = counts of prior omissions being disclosed now

Low signal individually — useful for validating Form 4 completeness.

---

### N-PORT-P — fund monthly portfolio holdings (P1)

- **Cadence**: Monthly, 60-day lag. Only the third month of each fiscal quarter is made public.
- **Format**: XML with root `<edgarSubmission>`
- **Filename**: `primary_doc.xml`
- **Filer**: The fund's parent Trust CIK (not the ticker's CIK — SPY files under CIK 884394 for "State Street SPDR S&P 500 ETF Trust")

Sample: [SPDR S&P 500 ETF Trust, period end 2026-06-30, filed 2026-08-28](../../data/edgar/samples/nport_p/0001410368-26-089410/primary_doc.xml) — 504 holdings, $781.2B NAV.

Key elements (from real sample):

| XPath | Sample value |
|-------|--------------|
| `/edgarSubmission/headerData/submissionType` | `NPORT-P` |
| `/formData/genInfo/cik` | `0000884394` |
| `/formData/genInfo/regName` | `State Street(R) SPDR(R) S&P 500(R) ETF Trust` |
| `/formData/genInfo/regFileNumber` | `811-06125` |
| `/formData/genInfo/regLei` | `549300NZAMSJ8FXPQQ63` |
| `/formData/genInfo/seriesName` | `N/A` (single-series trusts leave this blank) |
| `/formData/genInfo/seriesLei` | `549300NZAMSJ8FXPQQ63` |
| `/formData/genInfo/repPdEnd` | `2026-09-30` (fiscal quarter end) |
| `/formData/genInfo/repPdDate` | `2026-06-30` (**this filing's reporting date**) |
| `/formData/genInfo/isFinalFiling` | `N` |
| `/formData/fundInfo/totAssets` | `783339902049.69` |
| `/formData/fundInfo/totLiabs` | `2151029942.93` |
| `/formData/fundInfo/netAssets` | `781188872106.76` |
| `/formData/fundInfo/borrowings/*` | Aggregated debt obligations by counterparty |
| `/formData/invstOrSecs/invstOrSec` | **Repeats — one per holding** |

Per-holding fields under each `<invstOrSec>`:

| Field | Sample | Notes |
|-------|--------|-------|
| `name` | `Aflac Inc` | Free text |
| `lei` | `549300N0B7DOGLXWPP39` | Legal Entity Identifier |
| `title` | `Aflac Inc` | Security title (may differ from issuer name) |
| `cusip` | `001055102` | 9-char |
| `identifiers/isin@value` | `US0010551028` | Also `ticker`, `other` |
| `balance` | `5551377.00000000` | Share count or principal amount |
| `units` | `NS` | `NS` = number of shares; `PA` = principal amount |
| `curCd` | `USD` | Currency of `valUSD` (yes, oddly named) |
| `valUSD` | `650898953.25000000` | USD market value |
| `pctVal` | `0.083321585405` | Fraction of NAV (**not** percent — 0.083 = 0.083%) |
| `payoffProfile` | `Long` | Also `Short` |
| `assetCat` | `EC` | `EC`=equity, `DBT`=debt, `MMFT`=MMF, `ABS`=ABS, `LN`=loan, `RE`=real estate, `DIR`=derivative |
| `issuerCat` | `CORP` | `CORP`=corporate, `GOV`=government, `MUN`=municipal, `PF`=private fund, `RF`=registered fund |
| `invCountry` | `US` | ISO 3166 |
| `isRestrictedSec` | `N` | 144A / restricted flag |
| `fairValLevel` | `1` | FASB fair value hierarchy 1/2/3 |
| `securityLending/isCashCollateral` | `N` | Sec lending flags |
| `securityLending/isLoanByFund` | `N` | |

Plus derivatives subsection `<derivativeInfo>` for options, futures, forwards, swaps.

**Volume expectations**: SPY has ~500 holdings. Large fund families file 100+ N-PORT-Ps in a monthly batch. VOO's parent trust (Vanguard Index Funds) covers multiple series in a single filing — expect 200+ holdings per series.

---

### 13F-HR — institutional holdings (P1)

- **Cadence**: Quarterly, 45-day lag. Every manager >$100M in Section 13(f) securities (US-listed equities + certain ADRs + options).
- **Format**: XML — split across `primary_doc.xml` (cover) + `{arbitrary}.xml` (info table, root `<informationTable>`)
- **Coverage**: ~5,000 institutions

Sample: [Berkshire Hathaway, filed 2026-08-14](../../data/edgar/samples/13f_hr/0001193125-26-352200/56757.xml)

Per-position fields under each `<infoTable>`:

| Field | Sample | Notes |
|-------|--------|-------|
| `nameOfIssuer` | `ALLY FINL INC` | Free text |
| `titleOfClass` | `COM` | Class label |
| `cusip` | `02005N100` | 9-char, sometimes without check digit |
| `value` | `577211815` | **In thousands USD** (multiply by 1,000 for actual $) |
| `shrsOrPrnAmt/sshPrnamt` | `12561737` | Share count OR principal amount |
| `shrsOrPrnAmt/sshPrnamtType` | `SH` | `SH`=shares, `PRN`=principal (bonds) |
| `investmentDiscretion` | `DFND` | `SOLE`=sole discretion, `DFND`=defined, `OTR`=other |
| `otherManager` | `4` | Reference to co-manager in the summary section |
| `votingAuthority/Sole` | `12561737` | Voting split |
| `votingAuthority/Shared` | `0` | |
| `votingAuthority/None` | `0` | |

**CUSIP → ticker resolution**: 13F reports CUSIP but we track by CIK/ticker. SEC publishes the [13F Securities List](https://www.sec.gov/divisions/investment/13flists.htm) quarterly — one CSV mapping CUSIP → issuer name → status. Not a CIK map; you still need a CUSIP-to-ticker overlay (commercial data or manual reconciliation).

**Limitations**: 13F is *long-only* (no shorts, no derivatives beyond calls). Missing US-listed foreign issuers below the threshold. Berkshire files a **confidential-treatment request** for some positions (excluded from public filing). Bridgewater famously reports only a fraction of its book due to hedge classifications.

---

### N-1A — fund registration (P1)

- **Cadence**: One-time (per fund family) — subsequent tweaks are N-1A/A amendments
- **Format**: HTML narrative (no structured XML for the substance)
- **Filename**: Look for `*_n1a.htm` or the largest HTML in the filing (main prospectus body). Post-effective 497K files carry the launched fund's summary.

Sample: [Ehealth-related N-1A 2014-04-04](../../data/edgar/samples/n_1a/0001571049-14-001055/t1400597_n1a.htm) — 1.8 MB HTML.

Key content (unstructured — needs regex/NLP):

- Cover page: fund family name, series/class enumeration, ticker(s), listing exchange
- Investment objective (1-2 sentences)
- Principal strategies (paragraph)
- Fees & expenses table (management fee, other expenses, total)
- Principal risks (bulleted, standardized categories)
- Portfolio manager(s) (name + tenure)
- Series IDs (`S000...`) and Class IDs (`C000...`) — pulled from the SGML wrapper's `<SERIES>` block

**Signal**: cluster analysis of N-1A filings over 60-90 days predicts emerging themes (e.g., wave of "AI infrastructure" N-1As in early 2024 → 2025 semiconductor ETF explosion).

### 497K — summary prospectus (P1)

- **Cadence**: On or around effective date — the moment a fund goes "live"
- **Format**: 2-4 page HTML summary
- **Filename**: `*_497k.htm`

Sample: [497K filing 2024-02-27](../../data/edgar/samples/497k/0001213900-24-017429/ea170216-04_497k.htm) — 354 KB HTML.

Content is a subset of the N-1A: objective, fees table, principal risks, performance history (if any), portfolio manager, how to buy/sell. Structured enough that a template-based parser can pull fund_name + ticker + expense_ratio + first-day-of-trading with high accuracy.

---

### SC 13D — activist beneficial ownership (P2)

- **Cadence**: 10 calendar days after crossing 5% ownership *with intent to influence*
- **Format**: HTML

Sample: [SC 13D exhibit 2007-01-11](../../data/edgar/samples/sc_13d/0000898822-07-000039/exhibit109.htm)

Item-numbered structure (parse Item 1 through Item 7 as sections):

- **Item 1**: Security and issuer
- **Item 2**: Identity and background of filer(s)
- **Item 3**: Source and amount of funds
- **Item 4**: Purpose of the transaction — **the alpha field**. States whether filer intends to seek board seats, propose transactions, engage management, etc.
- **Item 5**: Interest in securities (share count + %)
- **Item 6**: Contracts / arrangements
- **Item 7**: Material to be filed as exhibits

**Downgrade to 13G** allowed once filer's intent becomes passive.

### SC 13G — passive 5%+ (P2)

- **Cadence**: Same trigger as 13D but for institutional passive holders (index funds, insurers, brokers)
- **Format**: HTML, often filed as `.txt` in older years

Sample: [SC 13G/A 2010-02-16](../../data/edgar/samples/sc_13g/0001144204-10-007811/v173835_sc13ga.txt)

Same item structure as 13D but Item 4 declares passive intent. Filer types: institutional (Rule 13d-1(b)), passive investor (Rule 13d-1(c)), or exempt investor (Rule 13d-1(d)).

**Signal value**: aggregated 13G filings show which institutions hold 5%+ in a name — the "shareholder register" for large positions. Complements 13F (which is manager-level long positions, not necessarily 5%+).

---

### 8-K — material events (P2)

- **Cadence**: T+4 business days after any material event
- **Format**: HTML with iXBRL tags for structured items; press releases attached as `ex99*.htm` exhibits

Sample: [Apple 8-K 2026-07-30 (Q3 2026 earnings)](../../data/edgar/samples/8_k/0000320193-26-000018/a8-kex991q3202606272026.htm)

**Items** are numbered per SEC's item catalog. Each 8-K declares one or more items in the SGML wrapper's `<TYPE>` block and in the HTML body. Full item catalog:

| Item | Meaning |
|------|---------|
| 1.01 | Entry into material definitive agreement |
| 1.02 | Termination of material definitive agreement |
| 1.03 | Bankruptcy or receivership |
| 1.04 | Mine safety |
| 2.01 | Completion of acquisition / disposition |
| 2.02 | Results of operations (earnings) |
| 2.03 | Creation of a material financial obligation |
| 2.04 | Triggering event under a financial obligation |
| 2.05 | Costs from exit / disposal activities |
| 2.06 | Material impairments |
| 3.01 | Delisting or noncompliance |
| 3.02 | Unregistered sale of equity |
| 3.03 | Material modification to shareholder rights |
| 4.01 | Change in registrant's certifying accountant |
| 4.02 | Non-reliance on previous financial statements |
| 5.01 | Change in control |
| 5.02 | Officer / director changes |
| 5.03 | Amendment to articles or bylaws / fiscal year change |
| 5.04 | Temporary suspension of trading |
| 5.05 | Amendments to code of ethics |
| 5.07 | Submission of matters to a vote |
| 5.08 | Shareholder director nominations |
| 7.01 | Reg FD disclosure |
| 8.01 | Other events |
| 9.01 | Financial statements and exhibits |

**Signal ranking (highest first)**: 5.02 (officer changes), 2.02 (earnings — but 10-Q/K supersede), 5.01 (change in control), 4.02 (restatement — negative), 1.01 (deal — usually pre-announced), 2.06 (impairment — negative), 7.01 (Reg FD — variable).

---

### N-CEN — annual fund census (P2)

- **Cadence**: Annual, 75-day lag
- **Format**: XML — root `<edgarSubmission>` (same shell as N-PORT but different form data)
- **Filename**: `primary_doc.xml`

Sample: [Mercer Funds 2022-06-09](../../data/edgar/samples/n_cen/0001320615-22-132521/primary_doc.xml)

Key elements (real sample):

| Field | Sample | Notes |
|-------|--------|-------|
| `submissionType` | `N-CEN` | |
| `schemaVersion` | `X0404` | |
| `investmentCompanyType` | `N-1A` | Also `N-2`, `N-3`, `N-4`, `N-5`, `N-6` |
| `seriesId` | `S000010041` | Series-specific — one N-CEN can cover many |
| `classId` | `C000027790` | Class-specific |
| `registrantFullName` | `Mercer Funds` | |
| `registrantLei` | `549300J7JB6PJ2JIIB11` | |
| `officeName` | `State Street Bank and Trust Company` | Adviser / sub-adviser / custodian sections |
| `feeWaivers/*` | (structured) | Fee-waiver disclosure |

**Use**: this is the reference layer for `ref.fund_registry`. Everything about who runs the fund (adviser, sub-adviser, custodian, auditor, fee arrangements) is here. N-CEN + N-PORT + N-1A/497K together fully describe a fund.

---

### 10-K — annual report (P3)

- **Cadence**: Annual (75 days for large accelerated filers, 90 for smaller)
- **Format**: HTML narrative body + inline XBRL + separate XBRL linkbases + auto-generated XLSX + rendered fact pages

Sample: [Apple 10-K FY25, filed 2025-10-31](../../data/edgar/samples/10_k/0000320193-25-000079/aapl-20250927.htm) — 1.5 MB HTML.

**Structure** (Items):

- Part I: Item 1 (business), Item 1A (risk factors), Item 1B (unresolved SEC comments), Item 2 (properties), Item 3 (legal proceedings), Item 4 (mine safety)
- Part II: Item 5 (market for common equity + repurchases), Item 6 (reserved), Item 7 (MD&A), Item 7A (market risk), Item 8 (financial statements), Item 9 (disagreements with accountants), Item 9A (controls), Item 9B (other)
- Part III: Item 10 (directors/officers), Item 11 (comp), Item 12 (security ownership), Item 13 (relationships/related), Item 14 (auditor fees)
- Part IV: Item 15 (exhibits)

**All financial line items are XBRL-tagged** in `dei:` (Document and Entity Information) and `us-gaap:` (GAAP) namespaces. Extracted sample tags: `dei:AmendmentFlag`, `dei:DocumentType=10-K`, `dei:DocumentAnnualReport=true`, `dei:DocumentPeriodEndDate=2025-09-27`, `dei:CurrentFiscalYearEndDate=--09-27`, `dei:EntityCentralIndexKey=0000320193`.

**Recommendation**: don't parse XBRL from the HTML. Use `data.sec.gov/api/xbrl/companyfacts/` for the same facts pre-parsed. Only touch the HTML if you need footnote text or MD&A narrative.

### 10-Q — quarterly report (P3)

Same shape as 10-K but shorter (unaudited financials, MD&A, Item 2 = market for common equity **including share buyback disclosures**, Item 4 = controls). XBRL-tagged.

Sample: [Apple 10-Q Q3 FY26](../../data/edgar/samples/10_q/0000320193-26-000020/aapl-20260627.htm)

**Buyback signal**: Item 2 of 10-Q lists share repurchases by month (Total Number of Shares Purchased, Average Price Paid, Total Number Purchased as Part of Publicly Announced Plans, Approximate Dollar Value Yet To Be Purchased). This is the *execution* data that pairs with 8-K buyback-plan announcements.

### DEF 14A — proxy statement (P3)

- **Cadence**: Annual, ahead of annual meeting
- **Format**: HTML + inline XBRL for structured comp tables (recently mandated)

Sample: [Apple DEF 14A 2026-01-08](../../data/edgar/samples/def_14a/0001308179-26-000008/aapl014016-def14a.htm)

Key sections:
- Summary Compensation Table (SCT) — CEO / CFO / top-3 NEO comp breakdown
- Pay-vs-Performance (PvP) — mandated 2023, ties comp to TSR
- Board composition, committees, independence
- Auditor ratification
- Shareholder proposals (activist / ESG)
- Say-on-pay results

### S-1 — IPO registration (P3)

- **Cadence**: Pre-IPO
- **Format**: HTML narrative (very long — hundreds of pages)

Sample: [S-1 exhibit 2015-05-12](../../data/edgar/samples/s_1/0001571049-15-004027/t1501001_ex1-2.htm)

Key sections:
- Prospectus summary
- Risk factors
- Use of proceeds
- Dividend policy
- Capitalization + dilution
- MD&A
- Management + comp
- Related-party transactions
- Principal shareholders
- Selected financial data
- Financials
- Underwriters

Precedes: 424B (pricing supplement), 8-A (registration to trade on an exchange), first 10-Q.

---

## XBRL primer

### Taxonomies

| Namespace | Purpose |
|-----------|---------|
| `us-gaap` | US GAAP financial concepts (Revenues, Assets, NetIncomeLoss, ...) |
| `ifrs-full` | IFRS concepts (foreign private issuers) |
| `dei` | Document and Entity Information — metadata (DocumentType, EntityCentralIndexKey, DocumentPeriodEndDate, EntityCommonStockSharesOutstanding) |
| `srt` | SEC Reporting Taxonomy — auxiliary (segments, geographic areas) |
| Custom (per-filer) | Extension concepts unique to a filer (`aapl:...`) — use sparingly |

### Contexts

Every XBRL fact has a *context*:
- **Instant**: point-in-time (`Assets` = balance-sheet snapshot)
- **Duration**: over a period (`Revenues` = flow over start–end range)

The `frames` API URL encodes this:
- Instant: `CY2024Q3I` (I suffix = instantaneous)
- Duration Q3: `CY2024Q3`
- Full-year: `CY2024`

### When to use each XBRL endpoint

| You want | Use |
|----------|-----|
| Every fact one filer has ever reported | `companyfacts/CIK{cik}.json` — big blob, 100KB-1MB per filer |
| One tag over time for one filer (time series) | `companyconcept/CIK{cik}/{taxonomy}/{tag}.json` — small, fast |
| One tag across all filers for one period (cross-section) | `frames/{taxonomy}/{tag}/{unit}/{period}.json` — great for peer comparison |

### Common us-gaap tags worth knowing

| Tag | Concept |
|-----|---------|
| `Assets`, `AssetsCurrent`, `AssetsNoncurrent` | Balance sheet |
| `Liabilities`, `LiabilitiesCurrent`, `StockholdersEquity` | Balance sheet |
| `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax` | Top line |
| `NetIncomeLoss`, `OperatingIncomeLoss` | Bottom line |
| `EarningsPerShareBasic`, `EarningsPerShareDiluted` | EPS |
| `CashAndCashEquivalentsAtCarryingValue` | Cash |
| `CommonStockSharesOutstanding`, `EntityCommonStockSharesOutstanding` | Shares out (dei namespace) |
| `PaymentsForRepurchaseOfCommonStock` | Buyback dollars |

Filers occasionally use different tags for the same concept (`Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax`); when building a time series, try multiple tags and prefer the one with the most data points.

### Amendments and restatements

`companyfacts` returns *all* filings that reported a tag, keyed by `accn` (accession). If a company restated, both the original and the restatement appear. Take the newest `filed` date per `(end, period)` pair to get the current version.

---

## Full-text search

Endpoint: `https://efts.sec.gov/LATEST/search-index?q=...&forms=...&dateRange=custom&startdt=...&enddt=...&ciks=...`

Parameters:
- `q` — query string (Lucene-ish; wrap phrases in double quotes)
- `forms` — comma-separated form types
- `dateRange=custom` + `startdt` + `enddt` — YYYY-MM-DD inclusive
- `ciks` — comma-separated 10-digit padded CIKs
- `from` — offset for pagination (page size = 10)

Response: `{"hits": {"total": {"value": ...}, "hits": [ {"_id": "{accession}:{doc_name}", "_source": {"form", "file_date", "display_names", "adsh", "ciks", ...}} ] }}`

**Use for discovery**, not enumeration. Search-index is not authoritative for listing all filings — the full-index is.

**Query examples**:
- `q='"single-stock"' forms=[N-1A]` → thematic ETF launch pipeline
- `q='"going concern"' forms=[10-K]` → distressed filers
- `q='"restatement" OR "restate"' forms=[8-K,10-K/A]` → accounting issues
- `q='"activist"' forms=[SC 13D]` → recent activism

---

## Parsing recipes

### Recipe 1 — `.idx` → row list

Already implemented in [`sources/edgar/filings.py::parse_idx`](../../src/factorlab/sources/edgar/filings.py). Handles both `form.idx` and `master.idx` column orderings. Returns `list[FilingRef]`.

### Recipe 2 — Form 4 XML → transaction rows

```python
import xml.etree.ElementTree as ET

def parse_form4(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    issuer = root.find("issuer")
    owner = root.find("reportingOwner")
    rows = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        rows.append({
            "issuer_cik": _text(issuer, "issuerCik"),
            "issuer_ticker": _text(issuer, "issuerTradingSymbol"),
            "owner_cik": _text(owner, "reportingOwnerId/rptOwnerCik"),
            "owner_name": _text(owner, "reportingOwnerId/rptOwnerName"),
            "is_officer": _text(owner, "reportingOwnerRelationship/isOfficer") == "1",
            "is_director": _text(owner, "reportingOwnerRelationship/isDirector") == "1",
            "transaction_date": _text(txn, "transactionDate/value"),
            "code": _text(txn, "transactionCoding/transactionCode"),
            "shares": float(_text(txn, "transactionAmounts/transactionShares/value") or 0),
            "price": float(_text(txn, "transactionAmounts/transactionPricePerShare/value") or 0),
            "acquired_disposed": _text(txn, "transactionAmounts/transactionAcquiredDisposedCode/value"),
            "shares_owned_after": float(_text(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value") or 0),
            "direct_or_indirect": _text(txn, "ownershipNature/directOrIndirectOwnership/value"),
        })
    return rows

def _text(el, path):
    x = el.find(path) if el is not None else None
    return x.text if x is not None else None
```

### Recipe 3 — N-PORT-P XML → holdings rows

```python
def parse_nport(xml_bytes: bytes) -> tuple[dict, list[dict]]:
    root = ET.fromstring(xml_bytes)
    gen = root.find(".//genInfo")
    fund = {
        "cik": _text(root, ".//headerData/filerInfo/filer/issuerCredentials/cik"),
        "reg_name": _text(gen, "regName"),
        "reg_lei": _text(gen, "regLei"),
        "series_id": _text(gen, "seriesId"),
        "series_lei": _text(gen, "seriesLei"),
        "series_name": _text(gen, "seriesName"),
        "reporting_date": _text(gen, "repPdDate"),
        "period_end": _text(gen, "repPdEnd"),
        "tot_assets": float(_text(root, ".//totAssets") or 0),
        "net_assets": float(_text(root, ".//netAssets") or 0),
    }
    holdings = []
    for h in root.findall(".//invstOrSec"):
        holdings.append({
            "name": _text(h, "name"),
            "lei": _text(h, "lei"),
            "cusip": _text(h, "cusip"),
            "isin": h.find("identifiers/isin").get("value") if h.find("identifiers/isin") is not None else None,
            "balance": float(_text(h, "balance") or 0),
            "units": _text(h, "units"),
            "val_usd": float(_text(h, "valUSD") or 0),
            "pct_val": float(_text(h, "pctVal") or 0),
            "payoff_profile": _text(h, "payoffProfile"),
            "asset_cat": _text(h, "assetCat"),
            "issuer_cat": _text(h, "issuerCat"),
            "inv_country": _text(h, "invCountry"),
            "fair_val_level": _text(h, "fairValLevel"),
        })
    return fund, holdings
```

### Recipe 4 — 13F info table XML → position rows

```python
def parse_13f_info_table(xml_bytes: bytes) -> list[dict]:
    # Namespace-free; if namespaced, register {'ns': 'http://www.sec.gov/edgar/document/thirteenf/informationtable'}
    root = ET.fromstring(xml_bytes)
    rows = []
    for it in root.findall(".//infoTable"):
        rows.append({
            "issuer": _text(it, "nameOfIssuer"),
            "class": _text(it, "titleOfClass"),
            "cusip": _text(it, "cusip"),
            "value_thousands_usd": float(_text(it, "value") or 0),   # NB: value is in $1000s
            "shares_or_principal": float(_text(it, "shrsOrPrnAmt/sshPrnamt") or 0),
            "sh_or_prn": _text(it, "shrsOrPrnAmt/sshPrnamtType"),      # SH or PRN
            "investment_discretion": _text(it, "investmentDiscretion"),
            "voting_sole": float(_text(it, "votingAuthority/Sole") or 0),
            "voting_shared": float(_text(it, "votingAuthority/Shared") or 0),
            "voting_none": float(_text(it, "votingAuthority/None") or 0),
        })
    return rows
```

### Recipe 5 — 8-K → item list (from HTML body)

8-K items are declared as boldface headings in the body: `Item 5.02 Departure of Directors or Certain Officers`. Regex-extractable:

```python
import re
ITEM_RE = re.compile(r"Item\s+(\d+\.\d{2})[\s\.\-—:]+(.+?)(?=\n|$)", re.MULTILINE)
items = [(m.group(1), m.group(2).strip()) for m in ITEM_RE.finditer(html_body)]
```

Cross-reference the numeric item code with the item catalog above to classify.

---

## Common pitfalls

- **Schema version drift** — Form 4 alone spans `X0202` (2003) through `X0609` (2026). Parsers must handle missing fields gracefully; don't assume newer schemas.
- **Amendments supersede but don't replace** — the original `4` stays in EDGAR when a `4/A` is filed. Store both, key on `(cik, accession)`, expose "current" via a view that takes newest `(period_of_report)` per issuer + owner.
- **CIK padding** — 10 digits in URLs (`CIK0000320193`), unpadded int in JSON responses (`320193`). Client handles this; downstream storage should always store as int.
- **CUSIP variants** — 13F uses 9-char CUSIP (with check digit); some feeds strip the check digit. Normalize before joining.
- **13F value is in thousands** — `value=577211815` means $577,211,815,000. Multiply by 1,000 before storing as $.
- **N-PORT pctVal is fractional, not percent** — `0.083321585405` means 0.083%, not 8.3%. Multiply by 100 for display.
- **Weekend / holiday gaps** — no daily-index published on non-trading days. Parser returns empty list; retry logic should skip forward, not fail.
- **Encoding** — post-2000 filings are UTF-8; pre-2000 often latin-1. `errors="replace"` on decode is safe.
- **Old filing 404s** — very old filings (pre-2005) may lack the `-index-headers.html` file; skip it silently.
- **Rate limit is per-IP, not per-key** — VPS + local dev share the 10 rps ceiling. Coordinate if both hit hard concurrently. SEC IP-bans (24h) on sustained abuse.
- **Fund CIK ≠ ticker CIK** — SPY (State Street SPDR Trust) files under CIK 884394. VOO files under CIK 36405 (Vanguard Index Funds). Look up via [company_tickers.json](https://www.sec.gov/files/company_tickers.json) which now includes fund-level tickers, then use the CIK for all downstream calls.
- **Series ID vs CIK** — a single N-1A / N-CEN filing can cover multiple series. Join fund reference data on series_id, not CIK.
- **XBRL tag drift** — `Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax`. Some companies also file custom extension tags. Prefer the tag with the most data points when building time series.
- **The `.txt` aggregate file** — every filing has an `{accession}.txt` that concatenates all documents in SGML. Don't download it unless you specifically want the SGML metadata — you'll double your storage.

---

## API surface (this repo)

Located at [`src/factorlab/sources/edgar/`](../../src/factorlab/sources/edgar/). Six modules built on one shared HTTP client. All read-only; no per-form parsers wired to DB yet (waits on migration).

| Module | Responsibility |
|--------|----------------|
| `client.py` | `EdgarClient` — UA-authenticated session, 10 rps throttle, 429/5xx retry with `Retry-After`, optional raw-archive hook |
| `filings.py` | Quarterly + per-day full-index parsing, filing URL construction, RSS URLs |
| `submissions.py` | `get_submissions(cik)` — every filing by a filer, with pagination |
| `companyfacts.py` | `get_company_facts`, `get_company_concept`, `get_frame` (XBRL) |
| `tickers.py` | `get_ticker_cik_map()`, `resolve_cik()` (in-process cached) |
| `search.py` | `search(query, forms=, date_range=)` — full-text |

Public exports from `factorlab.sources.edgar`:

```python
from factorlab.sources.edgar import (
    EdgarClient,
    get_daily_filings, get_quarterly_index, filing_base_url, filing_index_url, form_rss_url,
    get_submissions,
    get_company_facts, get_company_concept, get_frame,
    get_ticker_cik_map, resolve_cik,
    search,
)
```

### Env vars

| Var | Required | Purpose |
|-----|----------|---------|
| `EDGAR_USER_AGENT` | yes | Identifying UA per SEC policy. Format: `"FactorLab Research <email>"`. Client errors if missing. |

---

## Local reference — samples catalog

Every sample below was pulled from real EDGAR filings by [`playground/explore/edgar/sample_all_types.py`](../../playground/explore/edgar/sample_all_types.py). See [`data/edgar/samples/CATALOG.md`](../../data/edgar/samples/CATALOG.md) for the auto-generated per-form breakdown with extracted fields.

| Form | Sample filing | Priority | Notes |
|------|---------------|----------|-------|
| 4 | AAPL insider Sep 2026 | P1 | Rule 10b5-1 sale, `X0609` schema |
| 3 | eHealth new-insider 2006 | P1 | `X0202` schema (oldest) |
| 5 | Urban Outfitters CFO 2015 | P1 | Annual summary |
| NPORT-P | SPY trust 2026-06-30 | P1 | 504 holdings, $781.2B NAV |
| 13F-HR | Berkshire 2026 Q2 | P1 | Info table + primary_doc split |
| N-1A | 2014 fund registration | P1 | 1.8 MB HTML |
| 497K | 2024 summary prospectus | P1 | 354 KB HTML |
| SC 13D | 2007 activist filing | P2 | Item-structured HTML |
| SC 13G | 2010 passive filing | P2 | Filed as `.txt` |
| 8-K | AAPL Q3 2026 earnings | P2 | With iXBRL |
| N-CEN | Mercer Funds 2022 | P2 | `X0404` schema |
| 10-K | AAPL FY25 | P3 | 1.5 MB HTML + XBRL |
| 10-Q | AAPL Q3 FY26 | P3 | With buyback disclosure |
| DEF 14A | AAPL 2026 | P3 | Proxy + comp tables |
| S-1 | 2015 IPO | P3 | Underwriting exhibit only (body over ceiling) |
| XBRL frame | Assets CY2024Q4 | P2 | 6,267 filers |

---

## Schema mapping (target — post-migration)

Target tables land in a new `edgar` schema (or split across `market` / `ref` — TBD in migration design):

| Source form | Target table | Grain |
|-------------|--------------|-------|
| Full-index poll | `edgar.filings` | (accession, cik, form, filed_at, primary_doc_url) — event log |
| NPORT-P | `ref.fund_holdings` | (fund_cik, series_id, as_of_date, security_id, shares, market_value, pct_of_nav) |
| 13F-HR | `market.institutional_holdings` | (filer_cik, as_of_date, cusip, shares, value_thousands_usd) |
| Form 4 / 3 / 5 | `market.insider_transactions` | (issuer_cik, owner_cik, transaction_date, code, shares, price, is_officer, is_10b5_1) |
| N-1A / 497K | `market.fund_launch_events` | (cik, series_id, filed_at, effective_at, form) |
| SC 13D / 13G | `market.beneficial_ownership` | (issuer_cik, filer_cik, filed_at, pct_owned, filer_type, purpose_code) |
| 8-K | `market.material_events` | (cik, filed_at, items[], summary, exhibit_urls[]) |
| N-CEN | `ref.fund_registry` | (cik, series_id, class_id, adviser, custodian, auditor, fee_waivers, ...) |
| 10-K / 10-Q | (via companyfacts JSON only) | Not stored raw — EODHD Fundamentals covers financials |
| XBRL companyfacts | `market.fundamentals_xbrl` | (cik, taxonomy, tag, period, val) — optional; EODHD covers most cases |

All raw HTTP responses pushable through `storage.archive_http_response(...)` (same hook as EODHD) once the raw-archive table is scoped to EDGAR.

---

## Ingest strategy (post-migration, per form)

Every form family runs the same shape:

1. **Discover** — poll daily/quarterly index (or RSS for real-time forms). Insert new (accession, cik, form) rows into `edgar.filings`.
2. **Fetch** — for filings matching a form-type filter, download primary docs (see rule of thumb in "Filing archive structure"). Archive raw response.
3. **Parse** — form-specific parser transforms doc → typed rows (see "Parsing recipes").
4. **Upsert** — write into target table with `(cik, accession, row_key)` uniqueness. Immutable — amendments come as new accessions.

Planned cadences:

| Job | Cadence | Form types |
|-----|---------|------------|
| `us_edgar_index_daily.py` | Daily (post 22:00 ET) | All — populates `edgar.filings` |
| `us_edgar_launches_daily.py` | Daily (AM) | N-1A, N-1A/A, 497, 497K |
| `us_edgar_insider_daily.py` | Daily (T+1) | 4, 4/A, 3, 3/A, 5, 5/A |
| `us_edgar_ownership_daily.py` | Daily | SC 13D, SC 13D/A, SC 13G, SC 13G/A |
| `us_edgar_material_events_5min.py` | 5-min poll during market hours | 8-K |
| `us_edgar_nport_monthly.py` | Monthly (day after SEC posts) | NPORT-P |
| `us_edgar_ncen_monthly.py` | Monthly | N-CEN |
| `us_edgar_13f_quarterly.py` | Quarterly (45 days after quarter close) | 13F-HR, 13F-HR/A |

---

## Design notes

- **Read-only.** EDGAR is a public read source. No POSTs, no auth beyond UA.
- **Idempotent retries.** GETs are safe to retry — 5 attempts, backoff, `Retry-After`.
- **Archive-first.** Optional `storage=` hook mirrors EODHD's pattern — every response can be archived to raw store before parsing, enabling replay.
- **Full-text search is not authoritative for enumeration.** Use it for discovery; use the full-index for listing.
- **XBRL is optional.** Prefer `data.sec.gov/api/xbrl/*` JSON over parsing raw XBRL XML. Only touch the linkbase XMLs if a specific fact isn't in companyfacts.
- **Same client, three hosts.** The `EdgarClient` shares one throttle across www / data / efts because the 10 rps cap is per-IP, not per-host.

---

## Open questions

- **Schema location** — new `edgar.*` schema, or fold into `market.*` and `ref.*`? Decide during migration.
- **Raw archive** — reuse `market.raw_*` pattern, or new `edgar.raw_filings` (blobs) + `edgar.raw_documents` (per-doc)? Recommend the latter — filings have multiple docs, and per-doc caching helps re-parse.
- **N-PORT schema drift** — filers deviate. Probe with `edgartools` vs rolling our own during Phase 1.
- **13F CUSIP resolution** — SEC's [13F Securities List](https://www.sec.gov/divisions/investment/13flists.htm) is quarterly and covers CUSIP → issuer. Need overlay CUSIP → ticker → CIK; buy commercial or reconcile manually against `ref.securities`.
- **Form 4 schema versions** — parser must handle X0202 through X0609. Keep schema-version detection at the top of parser.
- **International ETF universe** — European UCITS ETFs file KIID docs, not N-1A. Separate stream; decide during Phase 2.
- **8-K item extraction accuracy** — regex on HTML body is imperfect. Cross-check against SGML `<TYPE>` block from `{accession}.txt` for validation.
- **Old-filing coverage** — pre-2005 filings often lack navigation files (404s). Filter or handle gracefully.

---

## References

- [SEC EDGAR Fair Access policy](https://www.sec.gov/os/accessing-edgar-data)
- [EDGAR Developer Resources](https://www.sec.gov/about/developer-resources)
- [XBRL API documentation](https://www.sec.gov/edgar/sec-api-documentation)
- [Full-index README](https://www.sec.gov/Archives/edgar/full-index/)
- [company_tickers.json](https://www.sec.gov/files/company_tickers.json)
- [13F Securities List (quarterly CUSIP registry)](https://www.sec.gov/divisions/investment/13flists.htm)
- [Form 4 XML schema](https://www.sec.gov/info/edgar/specifications/ownershipxmltechspec)
- [N-PORT technical specifications](https://www.sec.gov/info/edgar/specifications/form-nport-p-xml-technical-specifications)
- [N-CEN technical specifications](https://www.sec.gov/info/edgar/specifications/form-ncen-xml-technical-specifications)
- [8-K item list](https://www.sec.gov/fast-answers/answersform8khtm.html)
- Related internal: [`docs/research/etf-intelligence.md`](../research/etf-intelligence.md) · [`docs/data-sources/18-fund-flows-buybacks-shareholding.md`](18-fund-flows-buybacks-shareholding.md) · [`data/edgar/samples/CATALOG.md`](../../data/edgar/samples/CATALOG.md)
