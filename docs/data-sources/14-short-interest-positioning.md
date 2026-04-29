# Short Interest & Market Positioning

> Status: `[design]` · Last updated: 2026-04-29

## Purpose

Positioning data reveals crowding, fragility, and contrarian opportunities:
- **Short interest** — heavily shorted stocks face squeeze risk on positive catalysts
- **Margin debt** — aggregate leverage = systemic fragility indicator
- **Put/call ratio** — extreme readings are contrarian signals
- **India promoter pledging** — pledged promoter shares = forced liquidation risk

---

## Part 1 — Data Sources

### 1A. FINRA Short Interest (US — PRIMARY)

| Field | Detail |
|-------|--------|
| URL | https://www.finra.org/finra-data/browse-catalog/short-interest/data |
| Auth | None (public download) |
| Frequency | **Biweekly** — mid-month and end-of-month settlement dates |
| Format | CSV/text file download |
| Coverage | All US exchange-listed securities |

Published ~10 business days after settlement date. Fields: `Symbol`, `Settlement Date`, `Short Interest` (shares), `Avg Daily Volume`, `Days to Cover`

**Days to Cover** = Short Interest / Avg Daily Volume — the key metric. >5 days = crowded short.

### 1B. Finnhub Short Interest (SUPPLEMENTARY)

| Field | Detail |
|-------|--------|
| URL | https://finnhub.io/docs/api/stock-short-interest |
| Auth | Free API key |
| Coverage | US equities |

```
GET /api/v1/stock/short-interest?symbol=AAPL&from=2024-01-01&to=2025-01-01&token=KEY
```

### 1C. FINRA Margin Statistics (US)

| Field | Detail |
|-------|--------|
| URL | https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics |
| Frequency | Monthly |
| Format | PDF / manual extraction |

Total margin debt, free credit cash, credit balances. Rising margin debt at all-time highs = systemic fragility.

### 1D. CBOE Put/Call Ratio (US)

| Field | Detail |
|-------|--------|
| URL | https://www.cboe.com/us/options/market_statistics/ |
| Frequency | Daily |
| Format | CSV download |

Equity-only put/call > 1.0 = extreme fear (contrarian buy). < 0.5 = extreme greed (contrarian sell).

### 1E. NSE India — Shareholding Pattern & Promoter Pledging

| Field | Detail |
|-------|--------|
| URL | https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern |
| Frequency | **Quarterly** (within 21 days of quarter end) |
| Format | HTML / XBRL |
| Coverage | All NSE-listed companies |

SEBI quarterly shareholding pattern filings provide granular ownership breakdown far beyond just "Promoter / FII / DII / Public":

| Category | Sub-categories available |
|----------|------------------------|
| **Promoter** | Indian promoter, Foreign promoter, Promoter group, PACs (Persons Acting in Concert) |
| **FII/FPI** | Category I, II, III FPIs (different SEBI registration types) |
| **DII** | Mutual Funds, Insurance Companies (LIC etc.), Banks, NBFCs, Pension/Provident Funds, AIFs |
| **Public** | HNIs (>₹2L shares), Retail (<₹2L shares), Bodies Corporate, NRIs, Trusts, IEPF, Clearing Members |
| **Pledging** | Shares pledged, shares encumbered (non-pledge), total encumbered — for promoter group only |

**Why sub-category granularity matters:** "DII accumulation" is meaningless without knowing if it's LIC (passive, stable, rarely sells) vs mutual funds (flow-driven, can reverse on redemptions) vs banks (treasury, short-term). A PM needs MF + Insurance accumulation specifically, not DII as a blob.

**Promoter pledging > 50%** = red flag. Margin call → forced selling → stock collapse. Multiple Indian mid-caps have crashed 80%+ from promoter pledge unwinding.

**Also track:** Pledged shares vs encumbered (non-pledge) shares. Encumbrance includes non-disposal undertakings (NDU) given to lenders — different risk profile from margin-linked pledge.

**Data acquisition:** See [doc 09 Part 1F](09-insider-supply-chain.md) for tiered NSE/BSE acquisition strategy. For quarterly shareholding specifically, NSE bulk ZIP downloads are the most reliable free method — the format is standardized by SEBI XBRL taxonomy.

**Promoter regulation filings** (separate from quarterly SHP):
```
https://www.nseindia.com/companies-listing/corporate-filings-promoter-holding-reg29
```
Tracks event-driven promoter changes (pledge creation/release, acquisition/disposal) between quarterly filings. Cross-reference with PIT disclosures in [doc 09](09-insider-supply-chain.md).

### 1F. NSE India — FII/DII Derivatives Positioning

Available via `nselib`: open interest, participant-wise (FII/DII/Pro/Client) positions in futures and options.

---

## Part 2 — Key Signals

| Signal | Construction | Alpha Thesis |
|--------|-------------|--------------|
| **Short interest ratio** | Short interest / avg daily volume (days to cover) | >5 days + positive catalyst = squeeze |
| **Short interest change** | MoM change in short interest | Rapidly increasing shorts = market disagrees with bulls |
| **Margin debt delta** | MoM change in total margin debt | Declining from peak = deleveraging in progress |
| **Put/call extreme** | 5-day MA of equity put/call ratio | >1.0 = fear (buy), <0.5 = greed (sell) |
| **Promoter pledge %** (India) | Pledged shares / total promoter holding | >30% = caution, >50% = avoid |
| **Promoter stake change** (India) | QoQ change in promoter holding % | Declining promoter stake = loss of confidence |

---

## Part 3 — Schema

### `alt_research.short_interest`
```sql
ticker             text NOT NULL
settlement_date    date NOT NULL
short_interest     bigint                     -- shares short
avg_daily_volume   bigint
days_to_cover      numeric
source             text NOT NULL DEFAULT 'finra'
fetched_at         timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (ticker, settlement_date)
```

### `alt_research.shareholding_detail` (India — canonical, long-format)
```sql
id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
quarter_end        date NOT NULL
category           text NOT NULL              -- normalized category (see enum below)
holding_pct        numeric                    -- % of total equity
shares_held        bigint
num_holders        bigint
pledged_pct        numeric                    -- % of category holding pledged (promoter rows only, NULL otherwise)
encumbered_pct     numeric                    -- % encumbered non-pledge (promoter rows only, NULL otherwise)
source             text NOT NULL DEFAULT 'nse'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (ticker, quarter_end, category)
```

**Category enum values:**
```
-- Promoter group
'promoter_indian', 'promoter_foreign', 'promoter_group_other'

-- Foreign institutional
'fpi_cat1', 'fpi_cat2', 'fpi_cat3'

-- Domestic institutional
'mf'           -- Mutual Funds
'insurance'    -- Insurance Companies (LIC, ICICI Lombard, etc.)
'banks'        -- Banks (nationalised, private, foreign)
'nbfc'         -- NBFCs
'pension'      -- Pension/Provident Funds (EPFO, NPS)
'aif'          -- Alternative Investment Funds

-- Public / retail
'hni'          -- Individuals holding >₹2L nominal value
'retail'       -- Individuals holding ≤₹2L nominal value
'bodies_corp'  -- Non-institutional bodies corporate
'nri'          -- Non-Resident Indians
'trust'        -- Trusts
'iepf'         -- Investor Education and Protection Fund
'clearing'     -- Clearing Members
'other'        -- Any residual category
```

### `alt_research.shareholding_summary` (India — materialized view for screening)

Derived from `shareholding_detail` by aggregating sub-categories. Use for fast screening; drill into detail for investment memos.

```sql
-- Materialized view (or denormalized table refreshed on quarterly load)
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
quarter_end        date NOT NULL
promoter_pct       numeric                    -- SUM of promoter_indian + promoter_foreign + promoter_group_other
promoter_pledged_pct numeric                  -- weighted average of pledged_pct across promoter rows
fpi_pct            numeric                    -- SUM of fpi_cat1 + fpi_cat2 + fpi_cat3
dii_pct            numeric                    -- SUM of mf + insurance + banks + nbfc + pension + aif
mf_pct             numeric                    -- Mutual Funds alone (most important DII sub-category)
insurance_pct      numeric                    -- Insurance alone (LIC = structural holder)
public_pct         numeric                    -- SUM of all public categories
num_shareholders   bigint                     -- total across all categories
source             text NOT NULL DEFAULT 'nse'
fetched_at         timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (ticker, quarter_end)
```

---

---

## Part 3B — Composite Ownership Score (India)

A single sortable score per security, combining signals from multiple docs. Use for screening, then drill into components for the investment memo.

**Input signals** (each normalized to [-1, +1]):

| Component | Source Doc | Weight | Construction |
|-----------|-----------|--------|-------------|
| `promoter_change` | This doc (shareholding_detail) | 0.20 | QoQ change in total promoter holding_pct. Positive = increasing. |
| `pledge_risk` | This doc (shareholding_detail) | 0.15 | Inverse of pledged_pct. Low pledge = good. Scale: 0% pledge → +1, 50%+ → -1. |
| `insider_buy_net` | [Doc 09](09-insider-supply-chain.md) (india_insider_trades) | 0.20 | Net insider buy value (30d trailing) / free-float market cap. |
| `dii_accumulation` | This doc (shareholding_detail) | 0.15 | QoQ change in (mf_pct + insurance_pct). MF+Insurance rising = structural bid. |
| `fpi_flow` | [Doc 10](10-india-fii-fpi-flows.md) (fpi_daily_flows) | 0.10 | 30d rolling FPI net cash flow. Sector-level if available. |
| `block_deal_premium` | [Doc 18](18-fund-flows-buybacks-shareholding.md) (bulk_block_deals) | 0.10 | Block deal price vs CMP. Premium > 5% → +1, discount > 5% → -1. |
| `sast_event` | [Doc 09](09-insider-supply-chain.md) (india_insider_trades) | 0.10 | Binary flag: active open offer / creeping acquisition → +1, forced sell / delisting rejection → -1, none → 0. |

**Composite formula:**
```
ownership_score = Σ (weight_i × signal_i)

Range: [-1, +1]
Interpretation:
  > +0.5  → Strong ownership tailwind — promoter conviction + institutional accumulation
  +0.2 to +0.5 → Positive ownership dynamics
  -0.2 to +0.2 → Neutral
  -0.5 to -0.2 → Negative — promoter selling, pledge risk, FPI outflow
  < -0.5  → Strong ownership headwind — avoid or short
```

**Implementation:** Start with equal weights, then optimize on trailing 6-month forward returns using the `derived.factors` table once sufficient history exists.

**Temporal alignment:**
| Data | Frequency | Staleness at any point |
|------|-----------|----------------------|
| Insider trades (PIT) | Event-driven, T+2 disclosure | 0–2 days |
| Bulk/block deals | Daily | 0 days |
| FPI/DII flows | Daily | 0 days |
| Shareholding pattern | Quarterly | 0–90 days (stale mid-quarter) |
| SAST events | Event-driven | 0–5 days |

Mid-quarter, the shareholding components (`promoter_change`, `pledge_risk`, `dii_accumulation`) are stale. Use PIT disclosures and daily FPI flows as leading indicators to estimate mid-quarter drift.

---

## Part 4 — Implementation Order

### US
1. FINRA short interest CSV download + parser
2. CBOE put/call ratio daily
3. FINRA margin statistics monthly
4. Finnhub as API-based supplement

### India
5. NSE shareholding pattern — quarterly scraper using XBRL bulk downloads
   - Load into `shareholding_detail` (long-format)
   - Derive `shareholding_summary` materialized view
6. Composite ownership score — build after insider trades ([doc 09](09-insider-supply-chain.md)) and FPI flows ([doc 10](10-india-fii-fpi-flows.md)) are live

### Orchestration
```
factlab_india_ownership_quarterly   → shareholding pattern (run ~25th of Jan/Apr/Jul/Oct)
```
Daily ownership signals (PIT, bulk/block, FPI/DII) are handled by:
- `factlab_india_ownership_daily` — see [doc 09](09-insider-supply-chain.md)
- `factlab_india_fii_dii_daily` — see [doc 10](10-india-fii-fpi-flows.md)

---

## Open Questions

- [ ] FINRA short interest historical availability — how far back can we download?
- [x] ~~NSE shareholding XBRL parsing vs HTML scraping — which is more reliable?~~ — XBRL. SEBI mandates XBRL taxonomy for shareholding filings. More structured and stable than HTML.
- [ ] Securities lending data (borrow cost) — Quandl/Nasdaq had this, current source?
- [ ] Combine short interest + insider buying = high-conviction contrarian signal? (US)
- [ ] Composite ownership score: backtest required once shareholding_detail + india_insider_trades have 8+ quarters of history. Target: Nifty 500 universe.
- [ ] Screener.in API access: Screener supports custom screens like `Promoter holding > 50 AND Change in promoter holding > 0.1 AND Pledged percentage < 1`. Evaluate whether Screener Pro has API or export feature for automated screening.
