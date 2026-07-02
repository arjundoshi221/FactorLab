# Fund Flows, Share Buybacks & Institutional Shareholding

> Status: `[design]` · Last updated: 2026-04-29

## Purpose

Tracking where institutional capital is flowing — and where companies are deploying their own capital — reveals:
- **Passive flow pressure** — ETF inflows mechanically push up index constituents
- **Buyback signal** — companies are the largest informed buyers of their own stock. $800B+/yr in US.
- **Institutional ownership changes** — 13F filings reveal smart money positioning with a 45-day lag
- **India MF flows** — domestic mutual fund SIP flows are a structural, growing bid

---

## Part 1 — Data Sources

### 1A. ETF/Fund Flow Data (US)

| Source | Cost | Coverage | Notes |
|--------|------|----------|-------|
| ICI (Investment Company Institute) | Free | US mutual fund + ETF weekly flows | https://www.ici.org/research/stats — aggregate, not per-fund |
| ETF.com / ETFdb.com | Free browse | Per-ETF daily flow estimates | No API, scraping required |
| Finnhub Fund Ownership | Free tier | Institutional holders per stock | `GET /api/v1/stock/fund-ownership?symbol=AAPL` |

### 1B. SEC 13F Filings (US Institutional Holdings)

| Field | Detail |
|-------|--------|
| URL | EDGAR 13F filings |
| Auth | None |
| Frequency | **Quarterly** — filed within 45 days of quarter end |
| Coverage | All institutions managing >$100M in US equities |
| Tool | `edgartools` or SEC 13F XML parser |

**Key use:** Track what Berkshire, Bridgewater, Renaissance, etc. are buying/selling. 45-day lag limits alpha but reveals structural positions.

### 1C. Share Buyback Announcements (US)

| Source | Cost | Notes |
|--------|------|-------|
| EODHD Corporate Actions | $0 (on plan) | Buyback announcements in corporate actions feed |
| SEC filings (10-Q Item 2) | Free | Actual shares repurchased per quarter |
| Finnhub | Free | `GET /api/v1/stock/share-buyback?symbol=AAPL` |

**Signal:** Announced buyback = intent. Actual repurchases (from 10-Q) = execution. Companies that announce AND execute outperform those that announce but don't follow through.

### 1D. AMFI — India Mutual Fund Flows

| Field | Detail |
|-------|--------|
| URL | https://www.amfiindia.com/research-information/other-data/mf-scheme-performance |
| Auth | None |
| Frequency | Monthly |
| Coverage | All SEBI-registered mutual funds |

**Key data:**
- Monthly SIP inflows (structural domestic bid — currently ~₹25,000 Cr/month)
- Category-wise flows (large-cap, mid-cap, small-cap, sectoral)
- NFO (New Fund Offer) collections

### 1E. NSE Bulk/Block Deals (India)

| Field | Detail |
|-------|--------|
| URL | https://www.nseindia.com/report-detail/display-bulk-and-block-deals |
| Frequency | Daily |
| Format | HTML table / downloadable CSV (Bhav copy supplementary) |
| Coverage | All NSE-listed equities |

**Definitions:**
- **Bulk deal:** Transaction where total quantity traded ≥0.5% of total equity shares. Must be disclosed same day. Reveals large institutional entry/exit.
- **Block deal:** Minimum order of 5 lakh shares or ₹10 Cr, executed in a special 35-minute window (8:45–9:00 AM, 2:05–2:20 PM IST). Price within ±1% of current market. Used by large institutions to move size without impacting market.

**Key fields:** `Deal Date`, `Symbol`, `Client Name`, `Buy/Sell`, `Quantity`, `Price`, `Remarks`

**Signal value:**
- Block buy at premium to previous close → buyer conviction, willing to pay up
- Block buy at discount → seller urgency, buyer opportunistic
- PE/VC exit via bulk deal → known overhang removed, potentially bullish
- Promoter entity buying via bulk deal → stake consolidation signal (cross-reference with SAST in [doc 09](09-insider-supply-chain.md))

**Also available from BSE:**
```
https://www.bseindia.com/markets/equity/EQReports/BulkDeals.aspx
```

**Data acquisition:** NSE bulk/block deal data is available in the daily Bhav copy ZIP file as well as the dedicated report page. The Bhav copy approach is more stable than HTML scraping. Also available via `nselib` and Trendlyne API.

### 1F. India Shareholding Pattern (quarterly)

Already covered in [doc 14](14-short-interest-positioning.md) — now with granular sub-category breakdown (`shareholding_detail` long-format table). Also contains:
- **Mutual fund holding %** — growing domestic institutional ownership (separate from DII blob)
- **Insurance company holding %** — LIC is India's largest domestic investor (separate tracking)
- **FII/FPI holding %** — quarterly granularity by FPI category (daily flows in [doc 10](10-india-fii-fpi-flows.md))

### 1G. India Share Buybacks (SEBI Buyback Regulations)

| Field | Detail |
|-------|--------|
| URL | https://www.nseindia.com/companies-listing/corporate-filings-buyback |
| Frequency | Event-driven |
| Coverage | All NSE-listed companies conducting buybacks |

SEBI (Buy-Back of Securities) Regulations, 2018 require:
- **Tender offer route** (most common): company offers to buy back shares at a fixed premium. Promoter can participate. Max 25% of paid-up capital in any FY.
- **Open market route**: company buys via exchange over extended period. Max 15% of paid-up capital.

**Key fields from NSE filings:** `Company`, `Buyback Type` (tender/open market), `Offer Price`, `Max Shares`, `Max Amount`, `Record Date`, `Offer Open/Close Dates`, `Shares Accepted`, `Amount Utilized`

**Signal value:**
- Buyback at significant premium (>15%) to CMP → management believes stock is undervalued
- Repeated annual buybacks → consistent capital return, high FCF confidence
- Tender offer with high acceptance ratio → supply reduction, bullish
- Open market buyback with low utilization → announced but not executed (weak signal)

**Sources:** NSE corporate filings (event-driven), BSE similar page, Trendlyne API includes buyback announcements.

---

## Part 2 — Key Signals

| Signal | Construction | Alpha Thesis |
|--------|-------------|--------------|
| **Buyback yield** | Announced buyback $ / market cap | High buyback yield + insider buying = strong conviction |
| **Buyback execution rate** | Actual repurchases / announced amount | Companies that execute outperform announcers |
| **13F changes** | QoQ position changes by top funds | Crowded institutional longs = fragility, new positions = thesis |
| **ETF flow momentum** | 5-day ETF flow into sector ETFs | Positive flow momentum → sector outperformance |
| **India SIP flow trend** | MoM change in SIP inflows | Growing SIP = structural bid for mid/small-cap |
| **Bulk deal premium** | Block deal price vs market price | Premium = buyer conviction, discount = seller urgency |

---

## Part 3 — Schema

### `market.buyback_announcements`
```sql
buyback_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
announcement_date  date NOT NULL
buyback_amount     numeric                    -- authorized amount
buyback_shares     bigint                     -- authorized shares (if disclosed)
start_date         date
end_date           date
actual_repurchased numeric                    -- filled in from 10-Q data
source             text NOT NULL DEFAULT 'eodhd'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (ticker, announcement_date)
```

### `alt_research.fund_flows`
```sql
flow_date          date NOT NULL
category           text NOT NULL              -- 'us_equity_etf', 'us_equity_mf', 'india_sip', etc.
subcategory        text                       -- 'large_cap', 'mid_cap', 'small_cap', 'sector_tech', etc.
net_flow           numeric NOT NULL           -- positive = inflow
currency           text NOT NULL DEFAULT 'USD'
source             text NOT NULL              -- 'ici', 'amfi', 'etfdb'
fetched_at         timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (flow_date, category, subcategory)
```

### `alt_research.bulk_block_deals` (India)
```sql
deal_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
deal_date          date NOT NULL
deal_type          text NOT NULL              -- 'bulk', 'block'
client_name        text NOT NULL
buy_sell           text NOT NULL              -- 'buy', 'sell'
quantity           bigint NOT NULL
price              numeric NOT NULL           -- weighted avg price
traded_value_inr   numeric                    -- quantity * price
remarks            text                       -- exchange remarks if any
exchange           text NOT NULL DEFAULT 'NSE'
source             text NOT NULL DEFAULT 'nse' -- 'nse', 'bse', 'trendlyne'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (ticker, deal_date, deal_type, client_name, buy_sell, quantity)
```

### `market.india_buyback_offers`
```sql
buyback_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
announcement_date  date NOT NULL
buyback_type       text NOT NULL              -- 'tender', 'open_market'
offer_price        numeric                    -- for tender offers
max_shares         bigint
max_amount_inr     numeric                    -- authorized amount in INR
record_date        date
offer_open_date    date
offer_close_date   date
shares_accepted    bigint                     -- filled post-completion
amount_utilized_inr numeric                   -- actual spend
cmp_at_announcement numeric                   -- market price at announcement for premium calc
premium_pct        numeric                    -- (offer_price / cmp_at_announcement - 1) * 100
source             text NOT NULL DEFAULT 'nse'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (ticker, announcement_date, buyback_type)
```

### `alt_research.institutional_holdings` (13F — future)
```sql
filing_date        date NOT NULL
quarter_end        date NOT NULL
institution_name   text NOT NULL
institution_cik    text NOT NULL
ticker             text NOT NULL
shares             bigint
value_usd          numeric                    -- in thousands (as filed)
change_shares      bigint                     -- vs prior quarter
change_type        text                       -- 'new', 'increased', 'decreased', 'sold_out', 'unchanged'
source             text NOT NULL DEFAULT 'sec_13f'
fetched_at         timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (quarter_end, institution_cik, ticker)
```

---

## Part 4 — Implementation Order

### US
1. EODHD corporate actions — buyback announcements ($0 extra)
2. ICI weekly fund flows (US, free)
3. SEC 13F quarterly parsing (free, higher effort)
4. Finnhub fund ownership as supplement

### India
5. NSE bulk/block deal fetcher (daily) — Bhav copy ZIP or Trendlyne API
   - Write to `alt_research.bulk_block_deals`
   - Scheduler: `factlab_india_ownership_daily` (shared with PIT from [doc 09](09-insider-supply-chain.md))
6. AMFI monthly mutual fund flows (India, free)
7. India buyback offers — NSE corporate filings (event-driven)
   - Write to `market.india_buyback_offers`

---

## Open Questions

- [ ] 13F parsing at scale: ~5,000 institutions file quarterly. Start with top 50 by AUM?
- [ ] Buyback execution tracking: 10-Q Item 2 parsing needed. Structured in XBRL?
- [x] ~~India buyback data: SEBI requires buyback via tender offer or open market.~~ — Documented in 1G above. NSE corporate filings + Trendlyne API.
- [ ] Passive vs active flow: can we separate ETF creation/redemption from active fund flows?
- [ ] Bulk/block deal client name resolution: client names in NSE data are often broker names (e.g., "MORGAN STANLEY ASIA") not the ultimate beneficial owner. Need mapping table or ignore and focus on deal size + price signals.
- [ ] India buyback premium backtest: does >15% tender offer premium correlate with forward returns? Need historical NSE buyback data (2018+).
