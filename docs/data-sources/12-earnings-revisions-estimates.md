# Earnings Revisions & Analyst Estimates

> Status: `[design]` · Last updated: 2026-04-28

## Purpose

Earnings revisions are one of the most robust and persistent alpha factors in academic literature. Stocks with upward EPS revisions outperform; downward revisions underperform. The signal works because:
- Analysts revise slowly (anchoring bias) — the first upgrade predicts more upgrades
- Revision breadth (% of analysts revising up vs down) is a cleaner signal than the magnitude
- Revenue revisions are harder to game than EPS revisions

---

## Part 1 — Data Sources

### 1A. EODHD Estimates/Expectations API (PRIMARY — on our plan)

| Field | Detail |
|-------|--------|
| URL | https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds |
| Auth | API token (existing plan) |
| Coverage | US + global equities with analyst coverage |
| Data | EPS/Revenue estimates (annual, quarterly), actual vs estimate, # analysts |

**Endpoint:**
```
GET https://eodhd.com/api/fundamentals/AAPL.US?api_token=TOKEN&filter=Earnings
```

Returns: `Earnings::History` (actual vs estimate, surprise %), `Earnings::Trend` (current/next quarter/year estimates), `Earnings::Annual`

### 1B. Finnhub Estimates (SUPPLEMENTARY — free tier)

| Field | Detail |
|-------|--------|
| URL | https://finnhub.io/docs/api/company-eps-estimates |
| Auth | Free API key (60 req/min) |
| Endpoints | EPS estimates, revenue estimates, recommendation trends |

```
GET /api/v1/stock/eps-estimate?symbol=AAPL&freq=quarterly&token=KEY
GET /api/v1/stock/revenue-estimate?symbol=AAPL&freq=quarterly&token=KEY
GET /api/v1/stock/recommendation?symbol=AAPL&token=KEY
```

### 1C. Earnings Surprise Tracking

Both EODHD and Finnhub provide actual vs estimate data. Key fields:
- `eps_actual` vs `eps_estimate` → surprise %
- `revenue_actual` vs `revenue_estimate` → revenue surprise %
- Number of analysts covering → coverage breadth

---

## Part 2 — Key Signals

| Signal | Construction | Alpha Thesis |
|--------|-------------|--------------|
| **EPS revision breadth** | (# upgrades - # downgrades) / total analysts, trailing 30d | Most robust single revision factor |
| **Revenue revision** | Change in consensus revenue estimate / prior estimate | Revenue harder to manipulate than EPS |
| **Earnings surprise drift** | Stocks that beat estimates continue to drift up for 60 days (PEAD) | Post-earnings announcement drift |
| **Estimate dispersion** | Std dev of analyst estimates / mean | High dispersion = uncertainty = vol premium |
| **Coverage initiation** | New analyst coverage on previously uncovered stock | Attention catalyst for small caps |

---

## Part 3 — Schema

### `market.analyst_estimates`
```sql
estimate_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
period_type        text NOT NULL              -- 'quarterly', 'annual'
period_end         date NOT NULL              -- fiscal quarter/year end
metric             text NOT NULL              -- 'eps', 'revenue', 'ebitda'
consensus_estimate numeric
estimate_high      numeric
estimate_low       numeric
num_analysts       int
actual             numeric                    -- NULL until reported
surprise_pct       numeric                    -- (actual - estimate) / |estimate|
report_date        date                       -- earnings release date
source             text NOT NULL DEFAULT 'eodhd'
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (ticker, period_type, period_end, metric, source, fetched_at::date)
```

---

## Part 4 — Implementation Order

1. EODHD Earnings data extraction — already in our plan, $0 extra
2. Build revision tracker: snapshot estimates weekly, compute deltas
3. Finnhub as cross-validation source
4. PEAD signal: join earnings surprises with forward returns

---

## Open Questions

- [ ] Point-in-time estimates: need to snapshot consensus weekly to track revisions over time (EODHD gives current consensus only)
- [ ] India earnings estimates — Upstox doesn't provide. EODHD covers NSE-listed? Need to verify.
- [ ] Revision signal latency: how fast do EODHD estimates update after an analyst publishes?
