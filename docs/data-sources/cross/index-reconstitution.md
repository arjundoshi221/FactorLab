# Index Reconstitution Events

> Status: `[design]` · Last updated: 2026-04-28

## Purpose

When a stock is added to a major index, passive funds (ETFs, index funds) **must** buy it. When removed, they must sell. This creates predictable, mechanical capital flows that are:
- **Large** — S&P 500 passive AUM is ~$8T+. Nifty 50 passive AUM growing rapidly.
- **Predictable** — announcement date is before effective date. Trade the gap.
- **Decaying** — the alpha window between announcement and effective date is well-known but still trades

---

## Part 1 — Data Sources

### 1A. S&P Dow Jones Indices (US)

| Field | Detail |
|-------|--------|
| URL | https://www.spglobal.com/spdji/en/index-announcements/ |
| Auth | None (public announcements) |
| Frequency | Quarterly rebalance (3rd Friday of Mar/Jun/Sep/Dec) + ad hoc (M&A, delistings) |
| Format | Press releases, PDF |

**Key indices:** S&P 500, S&P MidCap 400, S&P SmallCap 600, S&P 100

Announcement → Effective date gap: typically 5-7 trading days. Add = buy pressure. Remove = sell pressure.

### 1B. Russell Reconstitution (US)

| Field | Detail |
|-------|--------|
| URL | https://www.lseg.com/en/ftse-russell/russell-reconstitution |
| Frequency | Annual (last Friday of June), with quarterly IPO adds |
| Impact | Largest single rebalancing event in US markets |

Russell 1000/2000/3000 — annual reconstitution creates massive volume. Preliminary lists published in May, final in June.

### 1C. NSE Index Changes (India)

| Field | Detail |
|-------|--------|
| URL | https://www.nseindia.com/regulations/index-reconstitution |
| Frequency | Semi-annual (Mar/Sep for Nifty 50/100/500) + quarterly for thematic |
| Format | NSE circulars (PDF) |

**Key indices:** Nifty 50, Nifty Next 50, Nifty 100, Nifty 500, Nifty Bank, Nifty IT

NSE publishes circulars ~4 weeks before effective date. Passive inflows to Indian index funds growing ~30% YoY — rebalancing impact increasing.

### 1D. MSCI Rebalancing (Global — EM)

| Field | Detail |
|-------|--------|
| URL | https://www.msci.com/index-reviews |
| Frequency | Quarterly (Feb/May/Aug/Nov) |
| Impact | MSCI EM inclusion/exclusion moves Indian stocks significantly |

MSCI India weight changes drive FPI flows directly. MSCI EM rebalance announcement → FPI flow in next 2-4 weeks.

---

## Part 2 — Key Signals

| Signal | Construction | Alpha Thesis |
|--------|-------------|--------------|
| **Index add** | Buy between announcement and effective date | Passive buying creates 2-5% drift |
| **Index remove** | Short/avoid between announcement and effective date | Passive selling creates -3-7% drift |
| **Nifty 50 add** (India) | Particularly strong due to growing passive AUM | 5-10% drift in India, less efficient market |
| **MSCI EM weight change** | Track India weight changes quarterly | Drives FPI allocation decisions |
| **Russell reconstitution** | Annual June event — largest rebalancing | Well-known but still creates opportunities in small-caps |

---

## Part 3 — Schema

### `alt_research.index_changes`
```sql
change_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
index_name         text NOT NULL              -- 'S&P 500', 'Nifty 50', 'MSCI EM', etc.
ticker             text NOT NULL
security_id        uuid REFERENCES ref.securities
change_type        text NOT NULL              -- 'add', 'remove'
announcement_date  date NOT NULL
effective_date     date NOT NULL
reason             text                       -- 'market_cap', 'merger', 'spin_off', 'delisting'
source             text NOT NULL              -- 'spglobal', 'nse_circular', 'msci'
source_url         text
fetched_at         timestamptz NOT NULL DEFAULT now()
UNIQUE (index_name, ticker, effective_date, change_type)
```

---

## Part 4 — Implementation Order

1. Manual tracking of S&P 500 + Nifty 50 changes (low volume, high impact)
2. NSE circular scraper for Indian index changes
3. S&P Global announcement scraper
4. MSCI quarterly review tracking
5. Backtest: returns between announcement and effective date

---

## Open Questions

- [ ] Automated circular parsing: NSE publishes PDFs — LLM extraction or regex on text?
- [ ] Pre-announcement prediction: can we predict Nifty 50 adds/removes from market cap + liquidity criteria?
- [ ] ETF flow data: can we get actual fund flow data to quantify passive buying pressure?
- [ ] Russell reconstitution: preliminary list available in May — how to systematically capture?
