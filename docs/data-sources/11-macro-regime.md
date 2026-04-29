# Macro Regime — Rates, Yields, Credit, Liquidity, Inflation

> Status: `[design]` · Last updated: 2026-04-28

## Purpose

Macro regime determines the tide. Individual stock selection is secondary when the discount rate, liquidity, and credit environment are shifting. This data drives:
- **Regime detection** — risk-on vs risk-off, reflationary vs deflationary
- **Sector rotation** — rate-sensitive (REITs, utilities, banks) vs rate-insensitive
- **Position sizing** — tighten in hostile regimes, expand in supportive ones

---

## Part 1 — Data Sources

### 1A. FRED (Federal Reserve Economic Data) — PRIMARY

| Field | Detail |
|-------|--------|
| URL | https://fred.stlouisfed.org/ |
| API | https://api.stlouisfed.org/fred/ |
| Auth | Free API key (instant signup) |
| Rate limit | 120 requests/minute |
| Format | JSON / XML |
| Python | `fredapi` (`pip install fredapi`) |
| Coverage | 800,000+ US & global time series |

**Key series:**

| Series ID | Name | Frequency | Signal Use |
|-----------|------|-----------|------------|
| `DFF` | Fed Funds Rate (effective) | Daily | Policy rate |
| `DGS2` | 2-Year Treasury Yield | Daily | Rate expectations |
| `DGS10` | 10-Year Treasury Yield | Daily | Discount rate proxy |
| `T10Y2Y` | 10Y-2Y Spread | Daily | Yield curve shape (inversion = recession) |
| `DFII10` | 10-Year TIPS (real yield) | Daily | Real discount rate |
| `BAMLH0A0HYM2` | HY OAS (ICE BofA) | Daily | Credit stress |
| `BAMLC0A0CM` | IG OAS | Daily | Investment grade stress |
| `M2SL` | M2 Money Supply | Monthly | Liquidity |
| `WALCL` | Fed Balance Sheet | Weekly | QE/QT proxy |
| `CPIAUCSL` | CPI (All Urban) | Monthly | Inflation |
| `PCEPI` | PCE Price Index | Monthly | Fed's preferred inflation |
| `DTWEXBGS` | Trade-Weighted USD | Daily | Dollar strength |
| `VIXCLS` | VIX (CBOE) | Daily | Implied vol / fear gauge |
| `UMCSENT` | Michigan Consumer Sentiment | Monthly | Consumer confidence |
| `UNRATE` | Unemployment Rate | Monthly | Labor market |
| `ICSA` | Initial Jobless Claims | Weekly | Real-time labor |
| `INDPRO` | Industrial Production | Monthly | Real activity |

```python
from fredapi import Fred
fred = Fred(api_key='YOUR_KEY')

# Example: 10Y-2Y spread
spread = fred.get_series('T10Y2Y', observation_start='2020-01-01')
```

### 1B. RBI (Reserve Bank of India) — India Macro

| Field | Detail |
|-------|--------|
| URL | https://www.rbi.org.in/Scripts/Statistics.aspx |
| API | https://api.rbi.org.in/ (limited) |
| Auth | None |
| Format | Excel/CSV downloads, some JSON API |

**Key series:** Repo rate, reverse repo, CPI India, WPI, IIP (Industrial Production), forex reserves, INR/USD reference rate

### 1C. EIA (US Energy Information Administration)

| Field | Detail |
|-------|--------|
| URL | https://api.eia.gov/ |
| Auth | Free API key |
| Coverage | Crude oil (WTI/Brent), natural gas, petroleum inventories |

**Key series:** `PET.RWTC.D` (WTI daily), `NG.RNGWHHD.D` (Henry Hub nat gas)

---

## Part 2 — Schema

### `alt_research.macro_series`
```sql
series_id          text NOT NULL              -- FRED series ID or custom
observation_date   date NOT NULL
value              numeric
source             text NOT NULL DEFAULT 'fred'  -- 'fred', 'rbi', 'eia'
fetched_at         timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (series_id, observation_date)
```

### `alt_research.macro_regimes` (derived)
```sql
regime_date        date PRIMARY KEY
regime_label       text NOT NULL              -- 'risk_on', 'risk_off', 'reflation', 'deflation'
yield_curve_signal text                       -- 'normal', 'flat', 'inverted'
credit_signal      text                       -- 'tight', 'normal', 'wide', 'stress'
liquidity_signal   text                       -- 'expanding', 'stable', 'contracting'
computed_at        timestamptz NOT NULL DEFAULT now()
```

---

## Part 3 — Implementation Order

1. FRED API integration — fetch daily/weekly/monthly series
2. Store in `alt_research.macro_series`
3. Compute regime labels from combination of signals
4. RBI data for India-specific macro
5. EIA for energy input costs

---

## Open Questions

- [ ] Regime detection model: rules-based vs HMM (Hidden Markov Model)?
- [ ] How many FRED series to track? Start with ~20 core, expand later
- [ ] RBI API reliability — may need to scrape Excel downloads instead
- [ ] Global rates (ECB, BOJ, BOE) — add when Europe/APAC phase starts
