# Regulatory & Policy Events

> Status: `[design]` · Last updated: 2026-04-28

## Purpose

Regulatory decisions create binary repricing events and structural sector shifts:
- **FDA approvals/rejections** — biotech stock moves of 30-80% on single decisions
- **Tariff/trade policy** — sector-level repricing (autos, semiconductors, agriculture)
- **Tax policy** — corporate tax rate changes create market-wide EPS step changes
- **Patent expirations** — predictable revenue cliffs for pharma companies
- **India: SEBI/RBI policy** — FPI taxation changes, margin rules, sector-specific regulation

---

## Part 1 — Data Sources

### 1A. FDA Calendar & Approvals (US Pharma/Biotech)

| Field | Detail |
|-------|--------|
| URL | https://www.fda.gov/drugs/development-approval-process-drugs |
| Calendar | PDUFA dates — FDA commitment dates for drug decisions |
| API | openFDA: https://open.fda.gov/apis/ |
| Auth | Free (API key optional for higher rate limits) |
| Coverage | All FDA drug/device decisions |

**Key datasets:**
- PDUFA calendar — scheduled drug approval decision dates
- Drug approval letters — actual approvals
- Complete Response Letters (CRLs) — rejections
- Orange Book — patent/exclusivity data (generic entry dates)

```
GET https://api.fda.gov/drug/drugsfda.json?search=brand_name:"Keytruda"
```

### 1B. ClinicalTrials.gov (Drug Pipeline)

| Field | Detail |
|-------|--------|
| URL | https://clinicaltrials.gov/api/v2/studies |
| Auth | None |
| Coverage | 500,000+ clinical trials worldwide |

Track Phase 3 trial completions → PDUFA filing → decision date. Pipeline visibility 1-3 years ahead.

### 1C. Federal Register (US Regulations & Tariffs)

| Field | Detail |
|-------|--------|
| URL | https://www.federalregister.gov/developers/api/v1 |
| Auth | None |
| Format | JSON API |
| Coverage | All proposed and final federal rules |

Search by agency (USTR for tariffs, EPA for environmental, FCC for telecom). Track proposed rules → comment period → final rule timeline.

### 1D. SEBI Circulars (India)

| Field | Detail |
|-------|--------|
| URL | https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListingAll=yes |
| Auth | None |
| Format | HTML listing + PDF circulars |

FPI taxation, margin requirements, insider trading rules, mutual fund regulations. Major SEBI circulars move Indian markets within hours.

### 1E. RBI Policy Announcements (India)

| Field | Detail |
|-------|--------|
| URL | https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx |
| Frequency | Bi-monthly MPC meetings + ad hoc |

Rate decisions, CRR/SLR changes, forex intervention signals, NBFC regulations.

### 1F. Patent Expiration Database

| Source | URL | Notes |
|--------|-----|-------|
| FDA Orange Book | https://www.fda.gov/drugs/drug-approvals-and-databases/approved-drug-products-therapeutic-equivalence-evaluations-orange-book | Patent + exclusivity expiry dates |
| Drugs@FDA | https://www.accessdata.fda.gov/scripts/cder/daf/ | Drug approval history |

Patent cliff = predictable. Revenue drops 80%+ when generic enters. Map expiry dates to pharma company revenue concentration.

---

## Part 2 — Key Signals

| Signal | Construction | Alpha Thesis |
|--------|-------------|--------------|
| **PDUFA binary event** | Long vol (straddle) before FDA decision date | 30-80% moves on approval/rejection |
| **Patent cliff timeline** | Map expiry dates to company revenue by drug | Short pharma companies facing near-term cliffs |
| **Tariff announcement** | NLP on Federal Register + trade policy news | Sector repricing: tariff target sectors underperform |
| **SEBI FPI tax change** | Monitor SEBI circulars for FPI-related changes | FPI tax increase → outflow, decrease → inflow |
| **RBI rate surprise** | Actual decision vs consensus expectation | Surprise cut = banks rally, NBFC rally. Surprise hold = sell-off |

---

## Part 3 — Schema

### `alt_research.regulatory_events`
```sql
event_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
event_type         text NOT NULL              -- 'fda_approval', 'fda_rejection', 'tariff', 'sebi_circular', 'rbi_rate', 'patent_expiry'
event_date         date NOT NULL
country            text NOT NULL DEFAULT 'US'
agency             text                       -- 'FDA', 'SEBI', 'RBI', 'USTR', etc.
title              text NOT NULL
description        text
affected_tickers   text[]                     -- array of tickers affected
affected_sectors   text[]                     -- GICS sectors affected
source_url         text
source             text NOT NULL
fetched_at         timestamptz NOT NULL DEFAULT now()
```

### `alt_research.patent_expirations` (pharma-specific)
```sql
patent_id          text PRIMARY KEY
drug_name          text NOT NULL
brand_name         text
ticker             text                       -- company ticker
patent_expiry      date NOT NULL
exclusivity_expiry date
estimated_revenue  numeric                    -- annual revenue at risk
source             text NOT NULL DEFAULT 'fda_orange_book'
fetched_at         timestamptz NOT NULL DEFAULT now()
```

---

## Part 4 — Implementation Order

1. FDA PDUFA calendar scraper — high-impact, free, structured
2. Orange Book patent expiry database — predictable revenue cliffs
3. SEBI circular tracker — India-specific regulatory risk
4. Federal Register API for tariff/trade policy
5. RBI MPC decision tracker

---

## Open Questions

- [ ] FDA decision prediction: can we use advisory committee (AdCom) votes as leading signal?
- [ ] India: PLI (Production Linked Incentive) scheme announcements — sector-level catalysts. Track via DPIIT?
- [ ] EU regulatory (EMA, ECB) — add when Europe phase starts
- [ ] Automate SEBI circular parsing: regex/LLM on PDF text to extract affected entities?
