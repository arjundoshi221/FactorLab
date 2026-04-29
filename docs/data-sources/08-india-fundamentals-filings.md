# India Fundamentals — NSE / BSE / SEBI Filings

> Status: `[design]` — research complete, endpoints identified, code not yet built

## Purpose

Insider trading disclosures, shareholding patterns, SAST (takeover) filings, bulk/block deals, and corporate actions for Indian listed equities. Covers all SEBI-mandated governance disclosures that feed into alpha signals (promoter activity, institutional flow, regulatory events).

**This is separate from Upstox (doc 02) which provides market data only.** Broker APIs in India (Upstox, Zerodha Kite, ICICI Breeze) expose zero fundamental or filing data.

---

## 1. Source Overview

Three canonical sources, each with different strengths:

| Source | Strength | API quality | Anti-scraping | Historical depth |
|--------|----------|-------------|---------------|------------------|
| **BSE** (`api.bseindia.com`) | Insider trades, shareholding, corp actions, financials | **Good** — clean REST JSON | **Low** — basic rate limiting | 3–5 years API, older via XBRL |
| **NSE** (`nseindia.com/api/`) | Same + bulk/block deals | Medium — hidden JSON, session cookies | **Aggressive** — CloudFlare, IP bans | 1–2 years web, archives older |
| **SEBI** (`sebi.gov.in`) | SAST takeover disclosures (canonical) | Poor — JSP HTML scraping | **Minimal** | 10+ years |

**Strategy: BSE first.** 95%+ of NSE-listed companies are dual-listed on BSE. BSE's API is significantly easier to work with. Use NSE for cross-validation or BSE-gap fills. SEBI only for SAST.

---

## 2. Data Categories

### A. Insider Trading (SEBI PIT Regulations 2015)

Disclosures under Prohibition of Insider Trading regulations:
- **Reg 7(1)** — Initial disclosure (within 30 days of becoming insider)
- **Reg 7(2)** — Continual disclosure (within 2 trading days if trade > Rs 10 lakh)
- **Reg 6(2)** — Disclosure by persons in possession of UPSI

**Fields available:**

| Field | Description |
|-------|-------------|
| Company name / scrip code | Listed entity |
| Person name | Insider |
| PAN | Partially masked |
| Category | Promoter / Promoter Group / Director / KMP / Designated Person / Immediate Relative |
| Security type | Equity Shares / Convertible Debentures / Warrants |
| Transaction type | Buy / Sale / Pledge / Revoke / Inter-se Transfer |
| Quantity | Number of securities |
| Value (INR) | Transaction value |
| Holdings before/after | Count + percentage |
| Transaction date | When trade occurred |
| Intimation date | When company was notified |
| Mode | Market Purchase / Off Market / ESOP / Rights |

### B. Shareholding Patterns (SEBI Reg 31 — Quarterly)

Filed within 21 days of quarter end. One of the cleanest Indian market datasets — audited, SEBI-mandated.

**Categories reported:**

| Category | Sub-categories |
|----------|---------------|
| **Promoter & Promoter Group** | Indian Promoters, Foreign Promoters |
| **Institutions** | Mutual Funds, FPIs/FIIs, Insurance, Banks, Pension Funds, AIF |
| **Non-Institutions** | Retail (small < Rs 2L), Retail (large > Rs 2L), NBFCs, NRIs, Trusts, HUF |
| **Custodians** | GDR/ADR depository receipts |
| **Pledged shares** | Promoter shares pledged or encumbered |

**Key derived signals:**
- Promoter holding change (QoQ) — declining = red flag, increasing = conviction signal
- Promoter pledge ratio — declining pledge = positive (deleveraging)
- FII/DII flow direction — tracks foreign/domestic institutional conviction
- Mutual fund buying patterns — herding vs. contrarian
- Retail participation changes — euphoria/panic indicator

### C. SAST Disclosures (SEBI Takeover Regulations 2011)

Substantial Acquisition of Shares and Takeovers — the canonical source for large block acquisitions and open offers.

| Regulation | Trigger | Disclosure |
|-----------|---------|------------|
| **Reg 29(1)** | Acquiring ≥ 5% | Initial disclosure within 2 working days |
| **Reg 29(2)** | Change of ≥ 2% (above 5%) | Continual disclosure within 2 working days |
| **Reg 30(1)/(2)** | Acquiring control | Disclosure to target + exchanges within 4 days |
| **Reg 31** | Quarterly shareholding | Reg 31(1) promoter, 31(4) top 10 holders |
| **Reg 32** | Open offer | Public announcement to acquire shares |

### D. Bulk & Block Deals

Large transactions that hit the tape — supplementary signal for institutional activity.

| Deal type | Definition | Reporting |
|-----------|-----------|-----------|
| **Bulk deal** | ≥ 0.5% of equity shares traded by a single entity in a day | Same-day disclosure |
| **Block deal** | Single trade ≥ 5 lakh shares or Rs 10 crore, executed in block window (08:45–09:00 IST) | Same-day disclosure |

### E. Corporate Actions (Bonus, Split, Rights, Dividends)

See [india-equities.md](../countries/india-equities.md) §6 — India has significantly more corporate actions than US peers. Adjustment factor history is essential.

---

## 3. API Endpoints

### BSE API (Primary — start here)

**Base URL:** `https://api.bseindia.com/BseIndiaAPI/api/`

No authentication required. Basic rate limiting (~1 req/sec is safe). Standard headers sufficient (User-Agent, Referer: `https://www.bseindia.com`).

#### Insider Trading
```
GET https://api.bseindia.com/BseIndiaAPI/api/InsiderTrading/w?scripcode={scrip}&fromdate={DD/MM/YYYY}&todate={DD/MM/YYYY}
```
Returns JSON array of insider trade disclosures.

#### Shareholding Pattern
```
GET https://api.bseindia.com/BseIndiaAPI/api/ShareholdingPattern/w?scripcode={scrip}&flag=Q&quarterEnd={DD/MM/YYYY}
```
`flag=Q` for quarterly. Quarter end dates: 31/03, 30/06, 30/09, 31/12.

#### Corporate Actions
```
GET https://api.bseindia.com/BseIndiaAPI/api/CorporateAction/w?scripcode={scrip}&segment=0
```

#### Company Info / Financials
```
GET https://api.bseindia.com/BseIndiaAPI/api/ComHeader/w?scripcode={scrip}
```

**BSE scrip code mapping:** BSE uses numeric scrip codes (e.g., 500325 = Reliance, 500180 = HDFC Bank). Cross-reference via ISIN — both NSE and BSE share ISINs for dual-listed stocks.

### NSE API (Secondary — cross-validation)

**Base URL:** `https://www.nseindia.com/api/`

**Critical: session cookie dance required.** NSE's website is a React SPA with hidden JSON APIs protected by CloudFlare.

```python
# Session setup pattern
import requests

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...',
    'Accept': 'application/json',
})
# Step 1: establish session cookies
session.get('https://www.nseindia.com', timeout=10)
# Step 2: now API calls work (for ~5 minutes)
```

#### Insider Trading (PIT)
```
GET https://www.nseindia.com/api/corporates-pit?index=equities&from_date=01-01-2025&to_date=31-03-2025
GET https://www.nseindia.com/api/corporates-pit?index=equities&symbol=RELIANCE
```

#### Shareholding Patterns
```
GET https://www.nseindia.com/api/corporate-shareholding?index=equities&symbol=RELIANCE
```

#### Bulk Deals
```
GET https://www.nseindia.com/api/bulk-deal
```

#### Block Deals
```
GET https://www.nseindia.com/api/block-deal
```

**NSE anti-scraping measures:**
1. Session cookies expire every ~5 minutes — must refresh
2. CloudFlare WAF — headless browser detection
3. Rate limiting — IP bans after ~200–300 requests per session
4. User-Agent validation — blocks non-browser UAs

**Mitigation:** 3–5 second sleep between requests. Cookie refresh every 50 requests. Rotate User-Agent strings. Always store raw responses so re-parsing is free.

### SEBI SAST (Takeover disclosures only)

**URL:** `https://www.sebi.gov.in/sebiweb/other/OtherAction.do`

```
POST https://www.sebi.gov.in/sebiweb/other/OtherAction.do
Content-Type: application/x-www-form-urlencoded

doTakeover=yes&companyName={name}&fromDate={DD/MM/YYYY}&toDate={DD/MM/YYYY}
```

Returns HTML. Parse with BeautifulSoup. No JSON API. Minimal anti-scraping (no CAPTCHA, lenient rate limits). Historical data back to 1997.

---

## 4. Rate Limits & Access

| Source | Auth | Rate limit | Session management |
|--------|------|------------|-------------------|
| **BSE API** | None | ~1 req/sec (safe), no hard documentation | Referer header recommended |
| **NSE API** | Session cookies | ~200–300 req/session, rotate every 5 min | Cookie dance required (see §3) |
| **SEBI** | None | ~1 req/sec (lenient) | None |

**Practical implications for a daily run:**
- Nifty 500 universe = 500 companies
- Insider trading (BSE): 500 requests → ~8 minutes at 1 req/sec
- Shareholding (BSE, quarterly): 500 requests → ~8 minutes (run quarterly only)
- SAST (SEBI): typically batch by date range, not per-company → ~50 requests/run
- Total daily budget: ~20 minutes for full Nifty 500 insider trading scan

---

## 5. Python Libraries Assessment

| Library | Status | Insider Trading | Shareholding | SAST | Verdict |
|---------|--------|----------------|-------------|------|---------|
| **jugaad-data** | Active, maintained | No | No | No | Price data only |
| **nselib** | Sporadic updates | Partial (breaks often) | Partial | No | Don't depend on it |
| **nsetools** | **Dead** since 2022 | No | No | No | Do not use |
| **bsedata** | Sporadic | No | No | No | Basic BSE quotes only |
| **OpenBB** | N/A for India filings | No (US only) | No | No | Not useful here |

**Conclusion: no existing library covers this.** Build custom scrapers in `src/factorlab/sources/bse/` and `src/factorlab/sources/sebi/`.

---

## 6. Schema Mapping

### `alt_political.insider_trades`

Insider trading fits the `alt_political` schema (governance / regulatory disclosures), consistent with US senator trades in the same namespace.

```sql
id                  uuid PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id       uuid NOT NULL REFERENCES ref.instruments
regulation          varchar(20) NOT NULL    -- '7_1_initial', '7_2_continual', '6_2_upsi'
person_name         text NOT NULL
person_category     varchar(30) NOT NULL    -- 'promoter', 'promoter_group', 'director', 'kmp', 'designated_person', 'immediate_relative'
security_type       varchar(30) NOT NULL    -- 'equity', 'convertible_debenture', 'warrant'
transaction_type    varchar(20) NOT NULL    -- 'buy', 'sell', 'pledge', 'revoke', 'inter_se_transfer'
quantity            bigint NOT NULL
value_inr           numeric(18,2)
price_per_share     numeric(18,6)
holdings_before_pct numeric(8,4)
holdings_after_pct  numeric(8,4)
transaction_date    date NOT NULL
intimation_date     date
mode                varchar(20)             -- 'market', 'off_market', 'esop', 'rights'
exchange_source     varchar(10) NOT NULL    -- 'nse', 'bse', 'both'
market              varchar(10) NOT NULL DEFAULT 'in'
currency            char(3) NOT NULL DEFAULT 'INR'
source              varchar(50) NOT NULL    -- 'bse_api', 'nse_api'
as_of_time          timestamptz NOT NULL
raw_response_id     uuid
created_at          timestamptz NOT NULL DEFAULT now()
updated_at          timestamptz NOT NULL DEFAULT now()
UNIQUE (instrument_id, person_name, transaction_date, transaction_type, quantity)
```

### `alt_political.sast_disclosures`

```sql
id                  uuid PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id       uuid NOT NULL REFERENCES ref.instruments
regulation          varchar(20) NOT NULL    -- '29_1', '29_2', '30_1', '30_2', '31', '32'
acquirer_name       text NOT NULL
acquirer_pac        text                    -- persons acting in concert
shares_before       bigint
shares_after        bigint
pct_before          numeric(8,4)
pct_after           numeric(8,4)
acquisition_mode    varchar(30)             -- 'open_market', 'preferential', 'open_offer', 'rights'
transaction_date    date
disclosure_date     date NOT NULL
open_offer_price    numeric(18,6)           -- NULL if not open offer
open_offer_shares   bigint                  -- NULL if not open offer
market              varchar(10) NOT NULL DEFAULT 'in'
currency            char(3) NOT NULL DEFAULT 'INR'
source              varchar(50) NOT NULL    -- 'sebi', 'nse', 'bse'
as_of_time          timestamptz NOT NULL
raw_response_id     uuid
created_at          timestamptz NOT NULL DEFAULT now()
updated_at          timestamptz NOT NULL DEFAULT now()
UNIQUE (instrument_id, acquirer_name, disclosure_date, regulation)
```

### `market.shareholding_patterns`

Shareholding patterns describe market-level ownership structure — fits naturally in `market` schema.

```sql
id                  uuid PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id       uuid NOT NULL REFERENCES ref.instruments
quarter_end         date NOT NULL           -- 31 Mar, 30 Jun, 30 Sep, 31 Dec
category            varchar(30) NOT NULL    -- 'promoter_indian', 'promoter_foreign', 'mf', 'fpi',
                                            -- 'insurance', 'banks', 'pension', 'aif', 'nri',
                                            -- 'retail_small', 'retail_large', 'nbfc', 'trust',
                                            -- 'huf', 'custodian_gdr'
shares_held         bigint NOT NULL
pct_total           numeric(8,4) NOT NULL
pledged_shares      bigint                  -- for promoter categories only
pledged_pct         numeric(8,4)
market              varchar(10) NOT NULL DEFAULT 'in'
currency            char(3) NOT NULL DEFAULT 'INR'
source              varchar(50) NOT NULL    -- 'bse_api', 'nse_api'
as_of_time          timestamptz NOT NULL
raw_response_id     uuid
created_at          timestamptz NOT NULL DEFAULT now()
updated_at          timestamptz NOT NULL DEFAULT now()
UNIQUE (instrument_id, quarter_end, category, source)
```

### `market.bulk_block_deals`

```sql
id                  uuid PRIMARY KEY DEFAULT gen_random_uuid()
instrument_id       uuid NOT NULL REFERENCES ref.instruments
deal_type           varchar(10) NOT NULL    -- 'bulk', 'block'
deal_date           date NOT NULL
client_name         text NOT NULL
transaction_type    varchar(10) NOT NULL    -- 'buy', 'sell'
quantity            bigint NOT NULL
price               numeric(18,6) NOT NULL
value_inr           numeric(18,2)
market              varchar(10) NOT NULL DEFAULT 'in'
currency            char(3) NOT NULL DEFAULT 'INR'
source              varchar(50) NOT NULL    -- 'nse_api', 'bse_api'
as_of_time          timestamptz NOT NULL
raw_response_id     uuid
created_at          timestamptz NOT NULL DEFAULT now()
updated_at          timestamptz NOT NULL DEFAULT now()
UNIQUE (instrument_id, deal_date, client_name, transaction_type, quantity)
```

### Raw archive tables

```sql
-- BSE raw responses
CREATE TABLE market.raw_bse_filings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    endpoint        varchar(200) NOT NULL,
    scrip_code      varchar(20),
    params          jsonb,
    payload         jsonb NOT NULL,
    parsed_into     varchar(100)
);

-- SEBI raw responses
CREATE TABLE alt_political.raw_sebi_filings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    endpoint        varchar(200) NOT NULL,
    params          jsonb,
    payload_html    text NOT NULL,
    payload_parsed  jsonb,
    parsed_into     varchar(100)
);
```

---

## 7. Pipeline Design

```
                         ┌─────────────────────┐
                         │  Daily (post-close)  │
                         └─────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     BSE Insider Trading    NSE Bulk/Block       SEBI SAST
     (all Nifty 500)        Deals (daily)        (weekly batch)
              │                    │                    │
              ▼                    ▼                    ▼
     market.raw_bse_filings       │          alt_political.raw_sebi_filings
              │                    │                    │
              ▼                    ▼                    ▼
     alt_political.         market.bulk_       alt_political.
     insider_trades         block_deals        sast_disclosures
              │
              │
              │    ┌─────────────────────────┐
              │    │  Quarterly (Q+21 days)  │
              │    └───────────┬─────────────┘
              │                ▼
              │    BSE Shareholding Patterns
              │    (all Nifty 500)
              │                │
              │                ▼
              │    market.shareholding_patterns
              │                │
              ▼                ▼
     ┌──────────────────────────────────┐
     │  derived.governance_features     │
     │  - promoter_holding_change_qoq   │
     │  - pledge_ratio_change           │
     │  - fii_flow_direction            │
     │  - insider_net_buy_30d           │
     │  - sast_activity_flag            │
     └──────────────────────────────────┘
```

### Script naming

Following the existing convention (`factlab_{market}_{purpose}`):

| Script | Frequency | Schedule | Purpose |
|--------|-----------|----------|---------|
| `factlab_india_filings.py` | Daily | 16:30 IST (post-close) | BSE insider trades + NSE bulk/block deals |
| `factlab_india_shareholding.py` | Quarterly | 22nd of Jan/Apr/Jul/Oct | BSE shareholding patterns (21-day filing deadline) |
| `factlab_india_sast.py` | Weekly | Sunday | SEBI SAST takeover disclosures |

### Data flow

```
.env  →  no credentials needed (BSE/SEBI are public)
  │
  ├─ BSE scrip code mapping  →  cross-ref via ISIN from ref.instruments
  │
  ├─ BSE API: /InsiderTrading/w?scripcode={scrip}&fromdate=...&todate=...
  │     → JSON response → raw_bse_filings → alt_political.insider_trades
  │
  ├─ BSE API: /ShareholdingPattern/w?scripcode={scrip}&flag=Q&quarterEnd=...
  │     → JSON response → raw_bse_filings → market.shareholding_patterns
  │
  ├─ NSE API: /api/bulk-deal, /api/block-deal  (with cookie session)
  │     → JSON response → market.bulk_block_deals
  │
  └─ SEBI: OtherAction.do (POST, HTML)
        → HTML → raw_sebi_filings → alt_political.sast_disclosures
```

---

## 8. Code Organization

```
src/factorlab/sources/
├── bse/
│   ├── __init__.py
│   ├── client.py              # BSE API session, rate limiting, raw response archival
│   ├── insider_trading.py     # Fetch + parse insider trade disclosures
│   ├── shareholding.py        # Fetch + parse quarterly shareholding patterns
│   └── scrip_mapping.py       # BSE scrip code ↔ ISIN ↔ instrument_id mapping
├── nse/
│   ├── __init__.py
│   ├── client.py              # NSE session cookie management, anti-scraping resilience
│   ├── bulk_block_deals.py    # Bulk and block deal data
│   └── insider_trading.py     # Cross-validation with BSE data (secondary)
├── sebi/
│   ├── __init__.py
│   ├── client.py              # SEBI HTML scraping, BeautifulSoup parsing
│   └── sast.py                # SAST takeover disclosures
└── upstox/                    # (existing — market data)
    ├── ...
```

---

## 9. BSE Scrip Code Mapping

BSE uses numeric scrip codes, not ISINs or trading symbols. Cross-reference is essential.

| Company | BSE Scrip | NSE Symbol | ISIN |
|---------|----------|------------|------|
| Reliance Industries | 500325 | RELIANCE | INE002A01018 |
| HDFC Bank | 500180 | HDFCBANK | INE040A01034 |
| TCS | 532540 | TCS | INE467B01029 |
| Infosys | 500209 | INFY | INE009A01021 |
| ICICI Bank | 532174 | ICICIBANK | INE090A01021 |

**Mapping strategy:**
1. BSE publishes a full company list: `https://www.bseindia.com/corporates/List_Scrips.aspx`
2. Download as CSV — contains scrip code, ISIN, company name, industry
3. Join to `ref.instruments` via ISIN (shared between NSE and BSE)
4. Store mapping in `data/in/bse_scrip_mapping.csv` (refresh monthly)

---

## 10. Edge Cases & Gotchas

1. **BSE date format** — `DD/MM/YYYY` (Indian format), not ISO. Easy to swap day/month silently.
2. **NSE sessions expire fast** — ~5 minutes. Any long-running job must refresh cookies mid-run.
3. **NSE has killed every major scraper** — nsetools (dead 2022), nsepy (dead 2021), jugaad-data (breaks periodically). Do not depend on third-party NSE wrappers.
4. **Dual-listed != identical data** — BSE and NSE filings for the same company can have minor timing/formatting differences. Use ISIN as the join key, not company name.
5. **Shareholding pattern timing** — filed within 21 days of quarter end, but some companies file late. Treat `as_of_time` (our ingestion time) as the knowledge date for backtesting.
6. **SAST HTML parsing is fragile** — SEBI's JSP pages have inconsistent table structures across years. Build robust parsers with fallback patterns.
7. **Pledge data** — only available for promoter categories in shareholding patterns. The pledged_shares field is NULL for non-promoter rows.
8. **Insider trade deduplication** — the same trade may appear in both NSE and BSE filings. Deduplicate on (instrument_id, person_name, transaction_date, quantity).
9. **Amount precision** — BSE reports values in INR (not lakhs/crores). NSE sometimes reports in lakhs. Normalize to INR everywhere.
10. **SEBI SAST historical format changes** — pre-2014 filings use a different HTML layout than post-2014. May need two parser paths.

---

## 11. Commercial Alternatives

If the scraping approach proves too fragile (especially NSE):

| Option | Coverage | Cost | Notes |
|--------|----------|------|-------|
| **NSE Data Products** (official) | Comprehensive, official feed | Enterprise pricing (lakhs/yr) | Contact `dataservices@nseindia.com` |
| **Trendlyne** | All filing types, aggregated | ~Rs 300–500/month (Pro) | No public API — web only |
| **Screener.in** | Financials + shareholding | Free / Premium | No public API |
| **Tickertape** (Smallcase) | Shareholding, insider activity | Free / Premium | No public API |
| **Quandl / Nasdaq Data Link** | Some India coverage | Paid | Spotty India data |
| **Refinitiv / CapitalIQ** | Comprehensive | Expensive | Overkill for personal use |

**Recommendation:** Start with BSE API (free, reliable). Only evaluate commercial options if BSE coverage proves insufficient or if NSE-specific data becomes critical.

---

## 12. Alpha Signal Potential

| Signal | Source | Frequency | Research evidence |
|--------|--------|-----------|-------------------|
| **Promoter buying** | Insider trades | Event-driven | Strong — promoters have information advantage, especially in mid/small caps |
| **Promoter pledge reduction** | Shareholding pattern | Quarterly | Positive — deleveraging reduces forced-selling risk |
| **FII/DII flow divergence** | Shareholding pattern | Quarterly | Useful as regime indicator (foreign vs. domestic conviction) |
| **Insider cluster buying** | Insider trades (aggregated) | Monthly rolling | Multiple insiders buying simultaneously = high conviction |
| **SAST open offer** | SAST disclosures | Event-driven | Immediate price impact — open offer sets a floor price |
| **Bulk deal institutional accumulation** | Bulk/block deals | Daily | Large block buys by known institutions = informed flow |
| **Promoter holding > 70%** | Shareholding pattern | Quarterly | Low free-float risk — thin liquidity, but strong commitment |
| **Mutual fund herding** | Shareholding pattern | Quarterly | Contrarian signal when combined with valuation |

---

## 13. Implementation Priority

| Phase | Deliverable | Dependency |
|-------|------------|------------|
| **1** | BSE scrip code mapping (`scrip_mapping.py`) | `ref.instruments` populated with ISINs |
| **2** | BSE insider trading scraper + DB table | Phase 1 |
| **3** | BSE shareholding pattern scraper + DB table | Phase 1 |
| **4** | SEBI SAST scraper + DB table | Phase 1 |
| **5** | NSE bulk/block deal scraper + DB table | NSE session cookie client |
| **6** | NSE insider trading (cross-validation) | Phase 5 client |
| **7** | Derived governance features | All above |

---

## 14. Open Questions

- [ ] BSE API endpoint verification — need to probe live to confirm current response shapes (anti-scraping may have changed since last check)
- [ ] BSE scrip code bulk download — is the CSV at `List_Scrips.aspx` still freely downloadable?
- [ ] NSE cookie management — should we use `jugaad-data`'s session handler as a dependency, or build our own?
- [ ] XBRL financials — is parsing BSE XBRL filings (using `arelle` or `python-xbrl`) worth the complexity for quarterly results, or better to scrape the JSON endpoints?
- [ ] Backfill depth — how far back do we need insider trading history? 2 years (BSE API) vs. 5+ years (manual scraping)?
- [ ] Schema namespace — insider trades in `alt_political` (governance data) vs. a new `governance` schema? Currently matches US senator trades pattern.
- [ ] Rate limit testing — need to empirically measure BSE's actual rate limits (documented nowhere)
- [ ] SEBI SAST parser — how many HTML layout variants exist across the 1997–2026 archive?

---

## References

- BSE India: https://www.bseindia.com/
- BSE Insider Trading: https://www.bseindia.com/corporates/Insider_Trading_new.aspx
- BSE Shareholding Patterns: https://www.bseindia.com/corporates/shpPattern.aspx
- BSE XBRL Filings: https://www.bseindia.com/corporates/xbrl_search.aspx
- NSE Insider Trading: https://www.nseindia.com/companies-listing/corporate-filings-insider-trading
- NSE Shareholding: https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern
- NSE Bulk Deals: https://www.nseindia.com/market-data/bulk-deals
- NSE Block Deals: https://www.nseindia.com/market-data/block-deals
- SEBI SAST: https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doTakeover=yes
- SEBI PIT Regulations 2015: https://www.sebi.gov.in/legal/regulations/jan-2015/securities-and-exchange-board-of-india-prohibition-of-insider-trading-regulations-2015_42164.html
- SEBI Takeover Regulations 2011: https://www.sebi.gov.in/legal/regulations/sep-2011/securities-and-exchange-board-of-india-substantial-acquisition-of-shares-and-takeovers-regulations-2011_20765.html
