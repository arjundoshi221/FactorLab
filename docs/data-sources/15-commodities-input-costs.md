# Commodities & Input Costs

> Status: `[design]` · Last updated: 2026-04-28

## Purpose

Commodity prices are the transmission mechanism between macro and micro:
- **Margin driver** — rising input costs compress margins for manufacturers, expand margins for producers
- **Inflation leading indicator** — commodity inflation leads CPI by 3-6 months
- **Sector rotation** — energy prices drive energy vs consumer discretionary relative performance
- **India specific** — crude oil imports = ~25% of India's import bill. INR weakens when oil rises.

---

## Part 1 — Data Sources

### 1A. EODHD Commodities (ALREADY ON OUR PLAN)

| Field | Detail |
|-------|--------|
| URL | EODHD historical data API with commodity tickers |
| Auth | Existing API token |
| Coverage | Major commodity futures — crude oil, gold, silver, copper, nat gas, etc. |

```
GET https://eodhd.com/api/eod/CL.COMM?api_token=TOKEN&from=2020-01-01
```

Commodity tickers: `CL.COMM` (WTI crude), `BZ.COMM` (Brent), `GC.COMM` (gold), `SI.COMM` (silver), `HG.COMM` (copper), `NG.COMM` (natural gas)

### 1B. EIA (US Energy Information Administration) — FREE

| Field | Detail |
|-------|--------|
| URL | https://api.eia.gov/v2/ |
| Auth | Free API key |
| Coverage | US energy production, consumption, inventories, prices |

**Key series:**
- Crude oil spot prices (WTI, Brent)
- Weekly petroleum status report (inventories — moves oil prices)
- Natural gas weekly storage report
- US crude production

### 1C. FRED Commodity Indices — FREE

| Series ID | Name | Use |
|-----------|------|-----|
| `DCOILWTICO` | WTI Crude Oil Spot | Energy input cost |
| `DCOILBRENTEU` | Brent Crude Spot | Global oil benchmark |
| `GOLDAMGBD228NLBM` | Gold (London Fix) | Safe haven / real rates proxy |
| `PCOPPUSDM` | Copper (global price) | Industrial demand proxy ("Dr. Copper") |
| `WPU101` | PPI — Iron & Steel | Steel input cost |
| `APU0000708111` | CPI — Gasoline | Consumer energy cost |

### 1D. USDA (Agriculture) — FREE

| Field | Detail |
|-------|--------|
| URL | https://quickstats.nass.usda.gov/api/ |
| Auth | Free API key |
| Coverage | Crop prices, production, stocks |

Relevant for agricultural companies (ADM, BG, DE) and Indian food inflation (wheat, rice, sugar, palm oil).

### 1E. Baltic Dry Index / Freight Rates

| Source | Cost | Notes |
|--------|------|-------|
| FRED `DBDI` (discontinued) | Free | Historical only |
| Freightos Baltic Index (FBX) | Free dashboard / paid API | Container shipping rates |
| Drewry WCI | Paid | Container rate benchmark |

---

## Part 2 — Key Signals

| Signal | Construction | Alpha Thesis |
|--------|-------------|--------------|
| **Oil vs sector margins** | Correlate WTI with airline, chemical, auto gross margins | Rising oil = short consumer, long energy |
| **Copper/gold ratio** | HG.COMM / GC.COMM | Rising = risk-on (industrial demand > safety). Falling = risk-off |
| **Input cost pressure** | Commodity price change vs company's COGS growth | Divergence = margin surprise coming |
| **Inventory builds** | EIA weekly petroleum inventories vs consensus | Surprise build = bearish oil, surprise draw = bullish |
| **India crude import bill** | Brent price × INR/USD × India import volume | Rising = INR pressure, current account deficit |

---

## Part 3 — Schema

### `alt_research.commodity_prices`
```sql
ticker             text NOT NULL              -- 'CL.COMM', 'GC.COMM', etc.
price_date         date NOT NULL
open               numeric
high               numeric
low                numeric
close              numeric
volume             bigint
source             text NOT NULL DEFAULT 'eodhd'
fetched_at         timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (ticker, price_date)
```

### `alt_research.commodity_inventories` (future)
```sql
report_date        date NOT NULL
commodity          text NOT NULL              -- 'crude_oil', 'natural_gas', etc.
inventory_level    numeric                    -- barrels / bcf
weekly_change      numeric
source             text NOT NULL DEFAULT 'eia'
fetched_at         timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (report_date, commodity)
```

---

## Part 4 — Implementation Order

1. EODHD commodity prices — already available, $0 extra
2. FRED commodity indices — supplement with free data
3. EIA petroleum inventories — weekly signal
4. Map commodities to sectors/companies for margin analysis
5. USDA agriculture data for India food inflation

---

## Open Questions

- [ ] Commodity-to-company mapping: which companies have highest input cost sensitivity? Build lookup table.
- [ ] MCX (India commodity exchange) data — available via Upstox or separate source?
- [ ] Futures curve (contango/backwardation) as signal — need front-month vs deferred month prices
- [ ] Freight rates: Freightos API pricing TBD
