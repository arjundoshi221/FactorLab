# Political Signals — US Senator (and House) Trades

> Status: `[design]`

## Purpose
US legislators are required to file equity transactions under the **STOCK Act of 2012** via Periodic Transaction Reports (PTRs). Aggregated, these have been studied as alpha sources (mixed but non-trivial evidence). Useful as a feature regardless — flow data with real signal in specific subsets (committee chairs in regulated sectors, etc.).

## Sources

### Primary (authoritative)
| Source | URL | Notes |
|--------|-----|-------|
| US Senate Office of Public Records | https://efdsearch.senate.gov | Senator filings, search-form-only access, PDF disclosures |
| US House Clerk | https://disclosures-clerk.house.gov | Representative filings, ZIP archives of XML/PDF |

Both publish Periodic Transaction Reports (PTRs), Annual Reports, and Amendments. Filings have a **45-day reporting window** — meaning by the time data is public, the transaction is already 1–45 days old. Build with this lag in mind; it is the binding constraint on any signal.

### Aggregators (faster than parsing filings yourself, vendor-dependent)
| Source | Notes |
|--------|-------|
| **Quiver Quantitative** (quiverquant.com) | Paid API; structured senator/house trades, lobbying, government contracts |
| **CapitolTrades.com** | Free site, structured table, scraping unclear ToS |
| **House Stock Watcher / Senate Stock Watcher** | Open-data projects; quality varies, often stale |
| **Unusual Whales** | Paid; aggregates politician trades + options flow |

Recommendation: **start by parsing the official disclosures directly** for Senate (efdsearch is awful but the data is canonical) and House (clerk.house.gov publishes ZIP archives of structured XML — easier). Fall back to an aggregator only if direct ingestion proves too slow.

## Auth
- **Senate efdsearch** — no auth, but requires accepting an agreement page (cookie-based session). Scraping must respect this.
- **House clerk** — no auth, public ZIPs.
- Aggregators (if used): API key in `.env` as `QUIVER_API_KEY` etc.

## Rate limits
- Senate site: undocumented; be polite (1 req/sec, rotate user agents only if necessary, identify yourself in UA per netiquette)
- House: bulk ZIP downloads; trivial
- Aggregators: vendor-specific

## Schema mapping (canonical)

`alt_political.legislator_trades`:
```sql
trade_id           uuid PRIMARY KEY DEFAULT gen_random_uuid()
chamber            text NOT NULL              -- 'senate', 'house'
legislator_id      text NOT NULL              -- canonical id (bioguide id where possible)
legislator_name    text NOT NULL
party              text                       -- 'D', 'R', 'I'
state              char(2)
filer_type         text                       -- 'self', 'spouse', 'dependent_child'
transaction_date   date                       -- when trade occurred
filing_date        date                       -- when disclosed (signal lag matters!)
ticker             text                       -- as filed (often messy)
security_id        uuid REFERENCES ref.securities    -- nullable until resolved
asset_description  text
transaction_type   text                       -- 'P' (purchase) | 'S' (sale) | 'E' (exchange) | partial sale
amount_min         numeric(18,2)              -- ranges, not exact: e.g. $1,001-$15,000
amount_max         numeric(18,2)
filing_url         text
raw_payload_id     uuid
fetched_at         timestamptz NOT NULL
as_of_time         timestamptz NOT NULL       -- POINT-IN-TIME — when WE knew
```

`alt_political.legislator_committees` (membership over time):
```sql
legislator_id      text
committee          text
role               text                       -- 'chair', 'member'
valid_from         date
valid_to           date
PRIMARY KEY (legislator_id, committee, valid_from)
```

Committee membership is critical context — a Banking Committee chair trading a bank stock is a very different signal than a back-bencher trading the same name.

## Pipeline

```
weekly (or daily during heavy filing windows):
        │
        ▼
fetch House clerk ZIP archives (XML inside) → raw_house
fetch Senate filings via search/download   → raw_senate
        │
        ▼
parse PDFs (Senate) and XML (House) → canonical legislator_trades rows
        │
        ▼
ticker resolution: filed-symbol → ref.securities (fuzzy + manual override table)
        │
        ▼
enrich with committee membership at filing_date
        │
        ▼
aggregate: legislator × month × net_position_change
           ticker × month × politician_net_buy_count
        │
        ▼
derived.political_features
```

## Storage
- Raw: `alt_political.raw_senate_filings`, `alt_political.raw_house_filings` (PDF/XML payloads as bytea, plus parsed jsonb)
- Fact: `alt_political.legislator_trades`
- Reference: `alt_political.legislators`, `alt_political.legislator_committees`
- Aggregates: `derived.political_features`

## Edge cases & gotchas
- **Reporting lag** — up to 45 days. A "buy on news" of a senator trade is not actually frontrunning; the filing date IS the signal date.
- **Amount ranges, not exact** — disclosures are bracketed ($1,001–$15,000, $15,001–$50,000, ...). Use midpoint with care, or treat as ordinal.
- **Spouses & dependents** — many trades are filed under a spouse. Treat as legitimate signal but tag.
- **Late / amended filings** — common. Original + amendments must both be stored; `as_of_time` distinguishes versions.
- **Ticker resolution** — filers write tickers inconsistently. "Apple Inc." / "AAPL" / "APPLE INC COM". Build a canonical-name + cusip-when-available resolver with a manual override table.
- **PDF parsing for Senate** — older filings are scanned PDFs. Use `pdfplumber` first, OCR as fallback (`pytesseract`). Quality varies by year.
- **Bulk transactions on the same filing** — one PTR can list many trades; preserve them as separate rows but link via `filing_id`.
- **Pelosi premium** — high-profile names attract massive social attention; consider whether copy-trading signals are alpha or just crowding-driven decay.

## Reference data
- Bioguide IDs (canonical legislator IDs): https://bioguide.congress.gov/
- Open-data projects with bulk extracts:
  - https://github.com/jeremiak/congress-trading-data
  - https://senatestockwatcher.com / https://housestockwatcher.com
  Use these as cross-checks against your direct parses, not as primary sources.

## Open questions
- [ ] Direct parsing vs. paid aggregator: which is the right tradeoff for v1? (Lean direct for House, evaluate aggregator for Senate.)
- [ ] OCR pipeline budget — older Senate scans require Tesseract or paid OCR (e.g. AWS Textract) for accuracy.
- [ ] Should we extend to lobbying disclosures (LD-2 forms) and government contracts in the same schema namespace?
- [ ] Comparable EU (MEPs) and India (parliamentary) disclosures — are they public? Phase 6+ scope.
- [ ] PEP scope creep — do we want governors, federal judges, fed governors? Probably not in v1.
