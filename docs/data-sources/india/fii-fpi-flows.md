# India — Foreign Investor Flows (FII/FPI/DII)

> Status: `[design]` · Last updated: 2026-04-29

## Purpose

Foreign Portfolio Investor (FPI, formerly FII) and Domestic Institutional Investor (DII) flows are the single most-watched macro signal in Indian equities. FPIs own ~17-20% of Indian listed market cap. Net FPI selling drives broad market drawdowns; sustained FPI buying supports rallies. At the sector level, fortnightly NSDL AUC data reveals where foreign capital is rotating.

---

## Part 1 — Data Sources

### 1A. NSE FII/DII Daily Report (PRIMARY — use this)

| Field | Detail |
|-------|--------|
| URL | https://www.nseindia.com/reports/fii-dii |
| Auth | None (public), but requires browser-like headers |
| Format | HTML table / downloadable CSV |
| Frequency | **Daily** — published after market close (~6:30 PM IST / 13:00 UTC) |
| Coverage | FII + DII net buy/sell in Cash + Derivatives segments |

**Data fields:** Date, Category (FII/FPI or DII), Buy Value (Cr), Sell Value (Cr), Net Value (Cr) — broken out by Cash and Derivatives segments.

**Programmatic access via Python libraries:**

| Library | Install | Function | Notes |
|---------|---------|----------|-------|
| [nsefin](https://pypi.org/project/nsefin/) | `pip install nsefin` | `get_fii_dii_activity()` | Python 3.9+, returns DataFrame |
| [nselib](https://pypi.org/project/nselib/) | `pip install nselib` | `fii_dii_trading_activity()` | Python 3.8+, clean DataFrames |
| [nsepython](https://pypi.org/project/nsepython/) | `pip install nsepython` | `fii_dii()` | Raw + Pandas modes |

**Recommended:** Start with `nselib` — most actively maintained, clean API surface.

```python
from nselib import capital_market

# Daily FII/DII activity
df = capital_market.fii_dii_trading_activity()
```

**Gotchas:**
- NSE frequently changes website structure — libraries may break without warning
- NSE blocks automated requests aggressively (rate limiting, Cloudflare-like protection)
- Libraries use browser-like session headers internally to work around blocks
- Always pin library version and test before deploying to cron

### 1B. NSDL FPI Portal (SECTOR-LEVEL — fortnightly)

| Field | Detail |
|-------|--------|
| URL | https://www.fpi.nsdl.co.in/web/Reports/ReportsListing.aspx |
| Auth | None |
| Format | HTML reports (static pages) |
| Frequency | **Fortnightly** (every 15 days) |
| Coverage | Sector-wise FPI investment + AUC (Assets Under Custody) |

**Key reports:**
- [Daily FPI investment trends](https://www.fpi.nsdl.co.in/Reports/Latest.aspx) — daily net equity + debt
- [Monthly FPI investment](https://www.fpi.nsdl.co.in/Reports/Monthly.aspx) — monthly aggregates
- [Fortnightly sector-wise data](https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx) — **the alpha data** — AUC by NSDL sector classification
- [Yearly FPI data](https://www.fpi.nsdl.co.in/Reports/Yearwise.aspx?RptType=6) — calendar year aggregates
- [Country-wise AUC (top 10)](https://www.fpi.nsdl.co.in/web/Reports/ReportDetail.aspx?RepID=14) — which countries are investing

**Sector-wise AUC is the key dataset.** It shows total FPI holdings by sector (Financial Services, IT, Oil & Gas, Pharma, FMCG, etc.) and how they shift fortnightly. Sector rotation by FPIs leads index rotation by 2-4 weeks.

**Access method:** HTML scraping with `requests` + `BeautifulSoup`. Pages are static HTML tables — no JS rendering needed.

**Legal note:** NSDL states reproduction/redistribution is prohibited. Use for internal research only, do not republish raw data.

### 1C. CDSL FPI Data (SUPPLEMENTARY)

| Field | Detail |
|-------|--------|
| URL | https://www.cdslindia.com/Publications/ForeignPortInvestor.html |
| Auth | None |
| Format | HTML tables |
| Frequency | Daily + monthly |

CDSL publishes parallel FPI data. NSDL + CDSL together cover 100% of FPI custody. For most purposes, NSDL alone is sufficient (larger share of FPI custody).

### 1D. SEBI FPI Reports (OFFICIAL AGGREGATE)

| Field | Detail |
|-------|--------|
| URL | https://www.sebi.gov.in/statistics/fpi-investment/fortnightly-sector-wise.html |
| Auth | None |
| Format | HTML / PDF |

SEBI consolidates NSDL + CDSL data. Use as validation/cross-check, not primary source.

### 1E. Third-Party Aggregators

| Source | Cost | API | Notes |
|--------|------|-----|-------|
| [Mr Chartist FII/DII Tracker](https://fii-diidata.mrchartist.com/) | Free | No API | Clean dashboard, manual use |
| [MacroMicro India FPI](https://en.macromicro.me/charts/33271/india-fpi) | Free browse | API paid | Charting + macro context |
| [TradingView NSDL Indicator](https://in.tradingview.com/script/mP6pGhR3-ILM-India-Sectors-NSDL-FII-FPI-Investments-Fortnightly/) | Free | Pine Script | FPI sector-wise on TV charts |
| [niftyeod.com FPI data](https://www.niftyeod.com/fpi-data/faq) | TBD | TBD | Cleaned FPI data with FAQ |

---

## Part 2 — What the Data Contains

### Daily FII/DII (from NSE)

| Column | Description |
|--------|-------------|
| date | Trading date |
| category | `FII/FPI` or `DII` |
| buy_value | Gross purchases (INR Cr) |
| sell_value | Gross sales (INR Cr) |
| net_value | Net = Buy - Sell (INR Cr) |
| segment | `Cash` or `Derivatives` |

### Fortnightly Sector-wise (from NSDL)

| Column | Description |
|--------|-------------|
| report_date | Fortnight end date |
| sector | NSDL sector classification (Financial Services, IT, Oil Gas & Consumable Fuels, etc.) |
| auc_value | Assets Under Custody (INR Cr) — total FPI holdings in sector |
| auc_pct | % of total FPI equity AUC |
| net_investment | Net FPI investment during fortnight |

---

## Part 3 — Schema (canonical)

### `alt_research.fpi_daily_flows`
```sql
flow_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
trade_date         date NOT NULL
category           text NOT NULL              -- 'FPI', 'DII'
segment            text NOT NULL              -- 'cash', 'derivatives'
buy_value_cr       numeric NOT NULL           -- INR crores
sell_value_cr      numeric NOT NULL
net_value_cr       numeric NOT NULL           -- buy - sell
source             text NOT NULL DEFAULT 'nse'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (trade_date, category, segment)
```

### `alt_research.fpi_sector_auc`
```sql
auc_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
report_date        date NOT NULL              -- fortnight end date
sector             text NOT NULL              -- NSDL sector classification
auc_value_cr       numeric                    -- total AUC in INR crores
auc_pct            numeric                    -- % of total equity AUC
net_investment_cr  numeric                    -- net FPI flow during fortnight
source             text NOT NULL DEFAULT 'nsdl'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (report_date, sector)
```

### `alt_research.fpi_country_auc` (future)
```sql
auc_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
report_date        date NOT NULL
country            text NOT NULL              -- 'USA', 'Luxembourg', 'Singapore', etc.
auc_value_cr       numeric
auc_pct            numeric
source             text NOT NULL DEFAULT 'nsdl'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (report_date, country)
```

---

## Part 4 — Signal Construction

### Macro signal: FPI flow momentum
```
daily_fpi_net_30d = SUM(net_value_cr) OVER trailing 30 calendar days WHERE category='FPI' AND segment='cash'

Signal states:
- Strong inflow:  daily_fpi_net_30d > +5,000 Cr  → bullish broad market
- Weak inflow:    0 < daily_fpi_net_30d < +5,000  → neutral-positive
- Weak outflow:   -5,000 < daily_fpi_net_30d < 0  → neutral-negative
- Strong outflow: daily_fpi_net_30d < -5,000 Cr   → bearish, defensives outperform
```

### Sector rotation signal: FPI AUC delta
```
sector_delta = (auc_pct[t] - auc_pct[t-1]) for each sector

Rising AUC % = FPIs increasing allocation (overweight signal)
Falling AUC % = FPIs reducing allocation (underweight signal)

Cross with Nifty sector indices for 2-4 week lead.
```

### DII counter-flow signal
```
When FPI net < 0 AND DII net > 0 → domestic institutions absorbing foreign selling → floor formation
When FPI net > 0 AND DII net < 0 → domestic institutions distributing into foreign buying → caution
```

---

## Part 5 — Implementation Order

### Phase 1 — Daily FII/DII via nselib (build now, free)
- Install `nselib` in factorlab conda env
- Create `src/factorlab/sources/nse/fii_dii.py` fetcher
- Daily cron: fetch after market close (13:30 UTC / 7:00 PM IST)
- Write to `alt_research.fpi_daily_flows`

### Phase 2 — NSDL Sector-wise AUC (scraper, free)
- Build `src/factorlab/sources/nsdl/sector_auc.py` HTML scraper
- Fortnightly cron: fetch after NSDL publishes (~1st and 16th of month)
- Write to `alt_research.fpi_sector_auc`
- Map NSDL sectors to Nifty sector indices for rotation signal

### Phase 3 — Country-wise AUC + enrichment
- Scrape country-wise AUC from NSDL
- Track which countries are increasing/decreasing India allocation
- Cross-reference with global macro (US rates, DXY, EM flows)

---

## Cross-References

This doc covers **aggregate flow data** (FPI/DII daily, sector-level AUC). Related ownership signals live in other docs:

| Signal | Doc |
|--------|-----|
| India insider trading (SEBI PIT/SAST) | [Doc 09 — Insider Trades & Supply Chain](09-insider-supply-chain.md) |
| Shareholding pattern (quarterly, sub-category) | [Doc 14 — Short Interest & Positioning](14-short-interest-positioning.md) |
| Bulk/block deals, India buybacks | [Doc 18 — Fund Flows, Buybacks & Shareholding](18-fund-flows-buybacks-shareholding.md) |
| Composite ownership score (combines all) | [Doc 14 — Part 3B](14-short-interest-positioning.md) |

FPI daily flow from this doc feeds into the composite ownership score as the `fpi_flow` component (weight 0.10). See [doc 14 Part 3B](14-short-interest-positioning.md) for the full scoring model.

---

## Open Questions

- [ ] `nselib` reliability — NSE blocks scrapers periodically. Need fallback (manual CSV download? backup library?). See [doc 09 Part 1F](09-insider-supply-chain.md) for tiered acquisition strategy.
- [ ] NSDL sector classification mapping to GICS/Nifty sectors — build lookup table
- [ ] Historical backfill depth — NSDL has data from 2002+, but HTML format varies by era. Start with 2020+ for clean structure.
- [x] ~~India insider trading (SEBI SAST regulations) — separate doc or add here?~~ — Added to [doc 09](09-insider-supply-chain.md) Parts 1D–1F. Includes PIT (Reg 7), SAST (takeovers/open offers), data acquisition strategy, and dedicated schema.
- [ ] Combine FPI flows with USD/INR and US 10Y yield for macro regime detection?
