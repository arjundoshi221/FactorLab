# Alternative Data — Insider Trades & Supply Chain Relationships

> Status: `[design]` · Last updated: 2026-04-29

## Purpose

Two complementary signals for fundamental equity research:
1. **Insider trading** — officers/directors/promoters buying and selling their own company's stock. Persistent alpha in cluster buys and large purchases relative to net worth. Covers US (SEC Form 4) and India (SEBI PIT/SAST regulations).
2. **Supply chain mapping** — who companies buy from and sell to. Enables propagation analysis (e.g., if Apple cuts orders, who gets hurt upstream?).

---

## Part 1 — Insider Trading (US — Form 4)

### 1A. EODHD Insider Transactions (USE THIS — already on our plan)

| Field | Detail |
|-------|--------|
| URL | https://eodhd.com/financial-apis/insider-transactions-api |
| Auth | API token (existing Fundamentals plan) |
| Cost | $0 extra — included in Fundamentals ($60/mo) or All-In-One ($100/mo) |
| Coverage | All US companies filing SEC Form 4 |
| Rate | Each request = 10 API calls; limit param 1–1000 (default 100) |

**Endpoint:**
```
GET https://eodhd.com/api/insider-transactions?code=AAPL.US&limit=100&from=2024-01-01&to=2025-12-31&api_token=TOKEN
```

**Key fields:** `filing_date`, `transaction_date`, `owner_name`, `owner_title`, `transaction_type` (P=Purchase, S=Sale), `transaction_shares`, `transaction_price`, `shares_owned_following`

**Why start here:** Zero incremental cost, structured JSON, fits existing EODHD pipeline. No parsing, no scraping.

### 1B. Alternative / Supplementary Sources

| Source | Cost | Quality | Notes |
|--------|------|---------|-------|
| [SEC EDGAR direct](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Free | Raw XML, needs parsing | Free EDGAR APIs, no auth needed |
| [edgartools](https://github.com/dgunning/edgartools) (Python) | Free | Parses Form 3/4/5 into Python objects | Best OSS library for raw EDGAR |
| [OpenInsider](http://openinsider.com/) | Free | Web screener, no API | Manual research / validation only |
| [sec-api.io](https://sec-api.io/docs/insider-ownership-trading-api) | ~$50/mo | Bulk JSONL downloads, real-time | Overkill since we have EODHD |
| [Finnhub](https://finnhub.io/docs/api/insider-transactions) | Free (60 req/min) | Per-ticker queries | Good free backup |

### 1C. Signal Construction

```
Event date = filing_date (public knowledge date)

Key signals:
- Cluster buys: 3+ insiders buying within 30 days
- Large purchases relative to compensation/holdings
- CEO/CFO buys > director buys (higher conviction)
- Purchase vs grant exercise (filter out routine option exercises)
- Transaction code: P (purchase) and S (sale) are primary signals
  - M (exercise), G (gift), A (award) are noise unless combined with P/S
```

---

## Part 1D — India Insider Trading (SEBI PIT Regulations)

SEBI Prohibition of Insider Trading (PIT) Regulations, 2015 require disclosure within **2 trading days** of any trade by connected persons. Structurally different from SEC Form 4 — different field set, different person categories, different transaction types.

**Who must disclose:**
- Promoters and promoter group
- Directors and KMP (Key Managerial Personnel)
- Designated persons and their immediate relatives
- Any person holding >10% of shares

**Regulation types:**
| Regulation | Trigger | Content |
|-----------|---------|---------|
| Reg 7(1) — Initial | Appointment as insider | Pre-existing holdings |
| Reg 7(2) — Continual | Any trade exceeding ₹10L in a quarter | Transaction details + pre/post holding |
| Reg 6(2) — Annual | End of financial year | Full year disclosure by all designated persons |

### Sources (Tiered by Reliability)

| Priority | Source | Method | Reliability | Cost |
|----------|--------|--------|-------------|------|
| 1 | **Trendlyne API** (Trendlyne Plus) | REST API, structured JSON | High — maintained product | ~₹8,000/mo (~$100) |
| 2 | **NSE Corporate Filings — Bulk Download** | Daily ZIP/CSV from nseindia.com/all-reports | Medium-high — machine-parseable | Free |
| 3 | **BSE Insider Trading Disclosures** | HTML tables from bseindia.com | Medium — less hostile than NSE | Free |
| 4 | **NSE PIT Filing Pages** (HTML scraping) | `nselib` / custom scraper | Low — breaks frequently | Free |

**Recommended approach:** Start with Trendlyne API if budget allows — it already maps PIT + SAST disclosures from both NSE and BSE into clean structured data. Fall back to NSE bulk downloads (daily ZIP files from the all-reports page are more stable than HTML scraping).

**NSE corporate filings page:**
```
https://www.nseindia.com/companies-listing/corporate-filings-insider-trading
```

**BSE PIT disclosures:**
```
https://www.bseindia.com/corporates/Insider_Trading_new.aspx
```

**NSE annual PIT disclosures:**
```
https://www.nseindia.com/companies-listing/corporate-filings-annual-pit-disclosures
```

### India PIT Signal Construction

```
Event date = disclosure_date (public knowledge date, not transaction date)

Key signals (ranked by alpha):
1. Promoter open-market purchase during price stress (price down >15% from 52w high)
2. Cluster buys: 3+ insiders buying within 30 days (same logic as US)
3. Promoter increasing stake via creeping acquisition (approaching regulatory thresholds)
4. Board-level accumulation: multiple directors buying simultaneously
5. Pledge release: promoter reducing pledged shares = improving financial health

Noise to filter:
- ESOP exercises (routine, not conviction)
- Gifts within promoter group (reshuffling, not buying/selling)
- Inter-se transfers between promoter entities
- Small transactions (<₹10L) by designated persons
```

---

## Part 1E — India Substantial Acquisition (SEBI SAST Regulations)

SEBI SAST (Substantial Acquisition of Shares and Takeovers) Regulations, 2011 govern acquisitions that cross ownership thresholds. **Higher alpha than PIT** — these are corporate-level strategic moves, not individual trades.

| Trigger | Threshold | Required Action |
|---------|-----------|-----------------|
| Initial trigger | Acquiring ≥25% voting rights | Mandatory open offer for additional 26% |
| Creeping acquisition | >5% in any FY (if already ≥25%) | SEBI + exchange disclosure |
| Voluntary open offer | Any acquirer | Open offer at premium to market |
| Delisting attempt | Promoter seeking to delist | Reverse book building process |
| Change in control | Change in management/control | Mandatory open offer |

**Why SAST > PIT for alpha:**

| | PIT (Insider Trading) | SAST (Substantial Acquisition) |
|---|---|---|
| Signal type | Individual conviction | Corporate strategic move |
| Typical size | ₹10L–₹10Cr | ₹100Cr–₹10,000Cr+ |
| Noise level | High (many small trades) | Low (each event is material) |
| Examples | Director buys 5,000 shares | PE firm acquires 26% triggering open offer |
| Alpha | Medium | **High** — open offers, takeovers, creeping acquisitions |

**Sources:** Same as PIT (NSE/BSE corporate filings, Trendlyne). SAST disclosures filed under:
```
https://www.nseindia.com/companies-listing/corporate-filings-takeover-open-offer
```

**Key SAST signals:**
- Open offer at significant premium to CMP → strong buy signal for target
- Creeping acquisition by promoter → increasing conviction, potential delisting candidate
- PE/strategic investor acquiring >10% → institutional validation
- Counter-offer by competing acquirer → bidding war, price upside
- Delisting attempt at floor price → evaluate fair value vs offer price

---

## Part 1F — India Data Acquisition Strategy

NSE/BSE websites are hostile to automated access (Cloudflare WAF, session tokens, aggressive rate limiting, frequent HTML structure changes). Plan accordingly.

**Practical tiered approach:**

| Tier | Source | When to Use | Maintenance Burden |
|------|--------|-------------|-------------------|
| **Primary** | Trendlyne API (paid) | Daily PIT + SAST monitoring | Low — API contract |
| **Secondary** | NSE bulk downloads (ZIP/CSV) | Quarterly shareholding, bulk/block deals | Medium — format changes occasionally |
| **Tertiary** | `nselib` / custom scrapers | FII/DII daily (see [doc 10](10-india-fii-fpi-flows.md)), ad-hoc | High — breaks regularly |
| **Fallback** | Manual CSV download + upload | When scrapers break | Guaranteed — human in the loop |

**Decision:** Evaluate Trendlyne Plus API cost (~₹8,000/mo) vs engineering time to maintain custom scrapers. For a PM running real capital, the API cost is trivially justified. For a research-phase project, start with NSE bulk downloads and upgrade when the scraper maintenance tax becomes painful.

**Legal considerations:**
- NSE/BSE terms restrict automated scraping. API access (Trendlyne) avoids this issue.
- NSDL explicitly prohibits redistribution of data. Internal research use only.
- All data is public regulatory filings — no legal risk in storing for internal analysis.

---

## Part 2 — Supply Chain Relationships

### 2A. Finnhub Supply Chain (START HERE — free tier)

| Field | Detail |
|-------|--------|
| URL | https://finnhub.io/docs/api/supply-chain-relationships |
| Auth | Free API key, 60 req/min |
| Cost | Free tier; premium $12–$100/mo for more coverage |
| Coverage | US public companies |

**Endpoint:**
```
GET https://finnhub.io/api/v1/stock/supply-chain?symbol=AAPL&token=KEY
```

Returns suppliers, customers, and relationship types. Free tier is sufficient to evaluate data quality for our universe.

### 2B. SEC 10-K DIY NLP (HIGH ALPHA, LONGER-TERM)

SEC mandates that companies disclose any customer representing >10% of revenue in their 10-K (Item 1 / Item 1A).

| Field | Detail |
|-------|--------|
| Source | SEC EDGAR full-text search |
| Auth | None (free public data) |
| Tool | [edgartools](https://github.com/dgunning/edgartools) + LLM extraction |
| Coverage | All US public companies with 10-K filings |

**Approach:**
1. Download 10-K filings via `edgartools`
2. Extract Item 1 (Business) and Item 1A (Risk Factors)
3. Use LLM (Claude API) to extract: customer names, supplier names, % revenue dependency
4. Map extracted names to tickers via EODHD symbol search
5. Build directed graph: Company → Customer/Supplier with weight = revenue %

**Alpha thesis:** Few retail investors build systematic supply chain graphs. When a major customer (e.g., Apple) reports weak guidance, propagate the signal to suppliers before the market fully prices it in.

### 2C. Enterprise Sources (out of budget)

| Source | Cost | Coverage | Notes |
|--------|------|----------|-------|
| [FactSet Revere](https://www.factset.com/marketplace/catalog/product/factset-supply-chain-relationships) | $10k+/yr | Global, most comprehensive | Gold standard — customers, suppliers, partners, competitors |
| Bloomberg SPLC | Terminal ($$$$) | Global | Not practical for our setup |

---

## Part 3 — Schema (canonical)

### `market.insider_trades`
```sql
trade_id           uuid PRIMARY KEY DEFAULT gen_random_uuid()
security_id        uuid REFERENCES ref.securities    -- resolved ticker
ticker             text NOT NULL                     -- raw ticker from source
filing_date        date NOT NULL                     -- when disclosed (signal date)
transaction_date   date                              -- when trade occurred
owner_name         text NOT NULL
owner_title        text                              -- CEO, CFO, Director, 10% Owner
transaction_type   text NOT NULL                     -- 'P' (Purchase), 'S' (Sale), 'M' (Exercise), etc.
shares             numeric
price              numeric
value              numeric                           -- shares * price
shares_owned_after numeric
source             text NOT NULL DEFAULT 'eodhd'
raw_payload        jsonb                             -- full API response for audit
fetched_at         timestamptz NOT NULL DEFAULT now()
-- Dedup
UNIQUE (ticker, filing_date, transaction_date, owner_name, transaction_type, shares)
```

### `alt_research.india_insider_trades`
```sql
id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
exchange           text NOT NULL              -- 'NSE', 'BSE'
regulation         text NOT NULL              -- 'pit_7_1' (initial), 'pit_7_2' (continual), 'pit_6_2' (annual), 'sast'
person_name        text NOT NULL
person_category    text NOT NULL              -- 'promoter', 'promoter_group', 'director', 'kmp', 'designated_person', 'immediate_relative'
transaction_type   text NOT NULL              -- 'buy', 'sell', 'pledge_create', 'pledge_release', 'encumbrance', 'revoke_encumbrance'
transaction_mode   text NOT NULL              -- 'market', 'off_market', 'esop', 'rights', 'preferential', 'creeping', 'open_offer', 'tender'
transaction_date   date
disclosure_date    date NOT NULL              -- signal date (public knowledge)
shares             bigint
value_inr          numeric
pre_holding_pct    numeric
post_holding_pct   numeric
pre_shares         bigint
post_shares        bigint
sast_threshold     text                       -- NULL for PIT; '5pct', '25pct', 'open_offer', 'delisting' for SAST
source             text NOT NULL              -- 'nse', 'bse', 'trendlyne'
raw_payload        jsonb
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (ticker, disclosure_date, person_name, transaction_type, shares)
```

### `market.supply_chain` (future)
```sql
relationship_id    uuid PRIMARY KEY DEFAULT gen_random_uuid()
source_security_id uuid REFERENCES ref.securities    -- the company
target_security_id uuid REFERENCES ref.securities    -- customer/supplier
source_ticker      text NOT NULL
target_ticker      text NOT NULL
relationship_type  text NOT NULL                     -- 'customer', 'supplier', 'partner'
revenue_pct        numeric                           -- % of source revenue (if disclosed)
confidence         numeric                           -- extraction confidence (NLP)
source             text NOT NULL                     -- 'finnhub', 'sec_10k_nlp'
valid_from         date
valid_to           date                              -- NULL = current
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (source_ticker, target_ticker, relationship_type, source, valid_from)
```

---

## Part 4 — Implementation Order

### Phase 1 — US Insider Trades via EODHD (build now, $0 extra)
- Add `insider_trades` table to market schema
- Write `src/factorlab/sources/eodhd/insider.py` fetcher
- Daily batch: fetch for all tickers in active universes
- Store in Postgres, export to Parquet

### Phase 2 — India PIT + SAST via Trendlyne or NSE bulk downloads
- Evaluate Trendlyne Plus API (cost vs coverage vs reliability)
- If API: write `src/factorlab/sources/trendlyne/insider.py` fetcher
- If bulk: write `src/factorlab/sources/nse/insider.py` using daily ZIP downloads
- Add `india_insider_trades` table to alt_research schema
- Daily fetch: PIT continual disclosures (Reg 7(2))
- Event-driven: SAST disclosures (open offers, threshold crossings)
- Scheduler: `factlab_india_ownership_daily` — fetch PIT + SAST + bulk/block deals

### Phase 3 — Finnhub Supply Chain (evaluate, free tier)
- Sign up for Finnhub free API key
- Explore supply chain endpoint for S&P 500 tickers
- Assess coverage and data quality before committing to schema

### Phase 4 — 10-K NLP Supply Chain (research project)
- Build `edgartools`-based 10-K downloader
- LLM extraction pipeline for customer/supplier mentions
- Directed graph construction and storage

---

## Open Questions

- [ ] Trendlyne Plus API: request trial access, evaluate coverage for Nifty 500 universe. Is PIT + SAST + bulk deals all included?
- [ ] Finnhub supply chain: how complete is coverage for mid/small-cap? Need to evaluate free tier.
- [ ] 10-K NLP: which LLM model balances cost vs accuracy for entity extraction? Claude Haiku likely sufficient.
- [x] ~~India insider trading: SEBI requires disclosure within 2 trading days. Source TBD~~ — Resolved: tiered approach (Trendlyne API → NSE bulk downloads → HTML scraping). See Part 1D–1F above.
- [ ] Cross-reference insider buys with supply chain shocks for compound signal?
- [ ] India PIT data quality: are NSE bulk download ZIPs consistently structured across years? Test with 2024–2026 range.
- [ ] SAST open offer premium analysis: need historical open offer prices vs CMP at announcement. Build from NSE takeover filings or Trendlyne.
