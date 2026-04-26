# India Equities — Market Structure & Regulations

> Status: `[active]` — updated April 2026

## 1. Exchange Structure

**Two equity exchanges, functionally one.**

| Exchange | Role | Volume share | Calendar |
|----------|------|-------------|----------|
| **NSE** (National Stock Exchange) | Primary | ~95% equity cash, ~100% derivatives | XBOM |
| **BSE** (Bombay Stock Exchange) | Secondary | ~5%, co-lists most large/mid names | XBOM |
| **MCX** (Multi Commodity Exchange) | Commodities | Commodity futures only | XBOM |

- NSE and BSE share the same holiday calendar (`XBOM` in `exchange_calendars`)
- `XNSE` does **not** exist in the library — always use `XBOM`
- Trading hours: 09:15–15:30 IST (375 one-minute candles per session)
- MCX: 09:00–23:30 IST (commodity futures, extended hours)
- Pre-open auction: 09:00–09:08 IST (price discovery), random close 09:08–09:12
- Post-close session: 15:40–16:00 IST (at closing price only)

**For quant work:** treat NSE as primary, BSE as ticker backup. Need a cross-reference table (same ISIN, different exchange tokens).

---

## 2. SEBI Market-Cap Classification

AMFI (Association of Mutual Funds in India) publishes semi-annual market cap classification used by all domestic mutual funds:

| Class | Rank by full m-cap | Count |
|-------|-------------------|-------|
| **Large cap** | Top 100 | 100 |
| **Mid cap** | 101–250 | 150 |
| **Small cap** | 251+ | ~2500+ |

**Why this matters:**
- Mutual funds are **mandate-bound** to specific buckets (large-cap fund must hold ≥80% in top 100)
- Stocks crossing the 100/250 boundaries see **forced buying/selling** on reclassification dates
- Model this as an event in the research framework — it's a predictable flow effect

---

## 3. Nifty Index Hierarchy

NSE Indices maintains a clean nested hierarchy:

| Index | Ranks | Count | Notes |
|-------|-------|-------|-------|
| **Nifty 50** | 1–50 | 50 | Benchmark. Top 5 ≈ 1/3 of index weight |
| **Nifty Next 50** | 51–100 | 50 | Less analyst coverage, momentum/PEAD edges |
| **Nifty 100** | 1–100 | 100 | **Phase 1 primary universe** |
| **Nifty Midcap 150** | 101–250 | 150 | SEBI mid-cap bucket |
| **Nifty Smallcap 250** | 251–500 | 250 | SEBI small-cap bucket |
| **Nifty 500** | 1–500 | 500 | ~95% of total NSE m-cap. Real investable universe |
| **Nifty Microcap 250** | 501–750 | 250 | Illiquid. Only if you have a microcap thesis |

**Nifty 50 concentration (April 2026 approx):**
- Reliance Industries ~9–10%, HDFC Bank ~6%, Bharti Airtel ~6%, SBI ~5%, ICICI Bank ~5%
- Top 5 = ~1/3 of index
- Sectors: Financial Services ~35%, Oil & Gas ~11%, IT ~9%, Autos ~7%, FMCG ~6%, Telecom ~5%

**Strategy indices** (~170 total): Alpha 50, Momentum 30, Quality 30, Low Vol 30, Alpha Low-Vol 30. Useful as factor benchmarks.

---

## 4. F&O (Futures & Options) Market

### Critical constraint: no overnight cash shorting

In India you **cannot short cash equities outside intraday**. To hold short positions overnight you need single-stock futures or options. SEBI restricts F&O eligibility to ~200 names, updated periodically.

**Any long/short strategy must be filtered to the F&O eligible list. Otherwise the backtest is fictional.**

### Key facts

| Aspect | Detail |
|--------|--------|
| F&O eligible stocks | ~200 (updated monthly by NSE circular) |
| Settlement | **Physically settled** since Oct 2019 (no cash settlement for stock F&O) |
| Index F&O | NIFTY (lot 65), BANKNIFTY (lot 30), FINNIFTY, MIDCPNIFTY |
| Weekly expiry | NIFTY (Thu), BANKNIFTY (Wed), FINNIFTY (Tue) |
| Monthly expiry | Last Thursday of month |
| Margin | SPAN + exposure margin, ~15–40% for stocks depending on volatility |

### Storage requirement
Store F&O eligibility as a **point-in-time membership table** (like index membership). The list changes — stocks get added/removed based on SEBI criteria (market cap, trading volume, delivery percentage).

---

## 5. Promoter Holdings & Ownership Structure

Indian companies have fundamentally different ownership structures than US peers.

| Metric | India (typical) | US (typical) |
|--------|----------------|-------------|
| Promoter/insider holding | 50–75% | 5–15% |
| Free float | 25–50% | 85–95% |
| Institutional (DII + FII) | 20–40% | 60–80% |

**Implications for FactorLab:**

- **Always store both total market cap and free-float market cap.** They diverge sharply.
- **Business groups matter.** Tata, Reliance, Adani, Birla, Bajaj, Mahindra, L&T, Vedanta, etc. Companies within a group are correlated through cross-holdings and shared funding.
- Add a `group` column to `ref.security` — it will matter for risk modeling and pairs work.
- **Pledged promoter shares** are a risk signal (quarterly disclosure). Declining pledge = positive signal.

---

## 6. Corporate Actions

Indian stocks have **significantly more corporate actions** than US peers. Adjustment factor history is essential, not optional.

| Action | Frequency | Impact |
|--------|-----------|--------|
| **Bonus issues** | Very common (e.g., 1:1 = stock doubles, price halves) | Must adjust historical prices |
| **Stock splits** | Common (face value change, e.g., Rs 10 → Rs 2) | Must adjust historical prices |
| **Rights issues** | Periodic, especially PSU banks | Dilution event |
| **Dividends** | Declared as Rs/share (not yield) | Minor price adjustment |

**Data source:** NSE publishes a clean corporate actions feed. Ingest from day one or adjusted prices will silently drift.

---

## 7. Settlement & Trading Rules

### Settlement
- **T+1** since January 2023 (previously T+2)
- T+0 optional in some names since 2024 (experimental, limited adoption)
- Affects funding cost assumptions in backtests

### Circuit breakers (individual stocks)

| Band | Trigger | Effect |
|------|---------|--------|
| 5% | Stock moves ±5% from previous close | Trading continues |
| 10% | Stock moves ±10% | 15-min cooling off |
| 20% | Stock moves ±20% | Trading halted for the day |

- Stocks hitting upper/lower circuit are **effectively non-tradable** for the rest of the day
- Your fill model must account for this — especially for small caps
- Store `circuit_limit_hit` flag in price data

### Market-wide circuit breakers (Nifty)

| Level | Trigger | Before 13:00 | 13:00–14:30 | After 14:30 |
|-------|---------|-------------|-------------|-------------|
| Level 1 | Nifty ±10% | 45 min halt | 15 min halt | No halt |
| Level 2 | Nifty ±15% | 1h 45m halt | 45 min halt | Trading halted for day |
| Level 3 | Nifty ±20% | Trading halted for day | Trading halted for day | Trading halted for day |

---

## 8. Currency & Tax

### Currency
- **INR** (Indian Rupee)
- FPI/FII flows are USD-denominated — creates currency sensitivity for foreign-held stocks

### Transaction costs

| Tax/Fee | Rate | Notes |
|---------|------|-------|
| **STT** (Securities Transaction Tax) | 0.1% buy + sell (delivery) | 0.025% sell only (intraday) |
| **Exchange transaction charges** | ~0.00325% | NSE |
| **SEBI turnover fee** | 0.0001% | |
| **Stamp duty** | 0.015% buy side | State-level, capped |
| **GST** | 18% on brokerage | Not on STT/stamp |

### Capital gains (as of Union Budget 2024)

| Type | Holding | Tax rate | Exemption |
|------|---------|----------|-----------|
| **LTCG** | > 12 months | 12.5% | First Rs 1.25 lakh/year exempt |
| **STCG** | ≤ 12 months | 20% | None |
| **Dividends** | — | Taxed as income | In hands of investor |

---

## 9. Data Model Implications for FactorLab

### ref.securities extensions
- `group` (varchar): Business group affiliation (Tata, Reliance, Adani, etc.)
- `free_float_pct` (numeric): Or derive from promoter + institutional holdings data
- `face_value` (numeric): Changes on splits (Rs 10 → Rs 2)

### market.price_bars extensions
- Consider storing `circuit_limit_hit` (boolean) per bar for fill model accuracy

### market.adjustment_factors
- **Critical for India** — more corporate actions than US
- Bonus issues and splits are frequent; must apply adjustment factors to all historical data
- Without this, any price-based analysis will be wrong

### universe.membership
- Track NSE and BSE listing status separately
- Track F&O eligibility as separate point-in-time membership
- Track SEBI market-cap classification (large/mid/small) with rebalance dates

### Timestamps
- All market data in `Asia/Kolkata` timezone
- Store as UTC with IST awareness (offset +05:30)
- Holiday calendar: **XBOM** (shared NSE/BSE). `XNSE` does not exist.

---

## 10. Key Differences from US Markets

| Aspect | US | India |
|--------|-----|-------|
| Settlement | T+1 (May 2024) | T+1 (Jan 2023) |
| Short selling | Available (borrow) | Intraday only (cash); F&O for overnight |
| Promoter holding | ~5–15% insider | ~50–75% promoter |
| Circuit breakers | Market-wide only (7/13/20%) | **Per-stock** (5/10/20%) + market-wide |
| Corporate actions | Occasional splits/dividends | Frequent bonus/split/rights |
| F&O settlement | Cash settled | **Physically settled** (stocks) |
| Capital gains tax | 0% LTCG, 37% max STCG (federal) | 12.5% LTCG, 20% STCG |
| Trading hours | 09:30–16:00 ET (6.5 hrs) | 09:15–15:30 IST (6.25 hrs) |
| Free float | 85–95% | 25–50% |
| Business groups | Uncommon (conglomerates exist) | Dominant (Tata, Reliance, Adani, etc.) |

---

## 11. Recommended Universe Phasing

| Phase | Universe | Count | Use case |
|-------|----------|-------|----------|
| **1** | Nifty 100 | 100 | Liquid, fast data sourcing, cross-sectional factor work |
| **2** | Nifty 500 ∩ F&O eligible | ~200 | Systematic long/short with real shortability |
| **3** | Full Nifty 500 | 500 | Long-only mid/small cap, more inefficiency |
| **Skip** | Microcap 250 | 250 | Unreliable fills, wide spreads. Revisit with transaction cost model |

---

## 12. Sectors Worth Modeling

Sectors with enough breadth for within-sector factor portfolios:

| Sector | Key dynamics |
|--------|-------------|
| **Banks (Private)** | HDFC, ICICI, Kotak, Axis, IndusInd. Credit growth, NIM, asset quality |
| **Banks (PSU)** | SBI, BoB, PNB, Canara. Policy-driven, different valuation regime |
| **IT Services** | TCS, Infosys, Wipro, HCL. Export-driven, USD/INR sensitive |
| **Pharma** | Sun, Dr. Reddy's, Cipla. US generics + domestic split |
| **Auto** | M&M, Tata Motors, Maruti, Bajaj. CV vs PV vs 2W dynamics |
| **Capital Goods / Infra** | L&T-led, government capex driven |
| **FMCG** | HUL, ITC, Nestle. Rural/urban consumption proxy |
| **Metals** | Tata Steel, Hindalco, JSW. Commodity beta |
| **PSUs** (cross-sector) | Separate factor structure. Policy-driven, periodic re-rating cycles |

**PSU vs Private** is itself a meaningful factor split — they trade on structurally different valuation regimes.

---

## References

- NSE Indices: https://www.niftyindices.com/
- SEBI: https://www.sebi.gov.in/
- AMFI: https://www.amfiindia.com/
- NSE Corporate Actions: https://www.nseindia.com/companies-listing/corporate-filings-actions
- Exchange Calendars (XBOM): https://github.com/gerrymanoim/exchange_calendars
