# 04 — Multi-Country Database Schema

> Living spec. Last updated: 2026-04-30.

## Overview

FactorLab uses a fully normalized PostgreSQL + TimescaleDB schema supporting multiple countries (India now, US next, then Europe/APAC). Postgres schemas organise the data:

| Schema | Purpose | Tables |
|--------|---------|--------|
| `ref` | Reference dimensions | countries, currencies, fx_pairs, fx_rates_daily, markets, exchanges, instruments, contracts, instrument_daily |
| `market` | Time-series fact tables | candles_1min (hypertable), candles_daily (hypertable), raw_responses |
| `universe` | Index definitions & membership | indexes, members |
| `alt_political` | US Congress trades + lobbying + contracts + donations + bills + hearings | 26 tables — country-tagged via `country_code` FK to `ref.countries.code` |

## Entity Relationship

```
ref.countries ──< ref.currencies
    │                  │
    ├──< ref.markets ──┤
    │        │         │
    │   ref.exchanges ─┤
    │        │         │
    │   ref.instruments ──< ref.contracts
    │        │                    │
    │   market.candles_1min ──────┘
    │   market.candles_daily ─────┘
    │
    └──< ref.fx_pairs ──< ref.fx_rates_daily

universe.indexes ──< universe.members ──> ref.instruments
```

## Tier 1: Reference Dimensions (ref)

### ref.countries
ISO 3166-1 country dimension. Seeds: IN, US, SG, GB, DE.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| code | CHAR(2) UNIQUE | ISO 3166-1 alpha-2 |
| name | VARCHAR(100) | |
| region | VARCHAR(20) | asia, americas, europe |
| timezone | VARCHAR(50) | Primary IANA timezone |

### ref.currencies
ISO 4217 currency dimension. Seeds: INR, USD, SGD, GBP, EUR.

| Column | Type | Notes |
|--------|------|-------|
| code | CHAR(3) PK | ISO 4217 |
| name | VARCHAR(50) | |
| symbol | VARCHAR(5) | ₹, $, S$, £, € |
| country_code | CHAR(2) FK | → countries.code |

### ref.fx_pairs
Full cross currency pairs (not just USD-normalized). Seeds: 10 pairs.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| base | CHAR(3) FK | → currencies.code |
| quote | CHAR(3) FK | → currencies.code |
| pair_code | VARCHAR(7) UNIQUE | USDINR, INRUSD, INRSGD |
| source | VARCHAR(30) | ecb, rbi, upstox |
| active | BOOLEAN | DEFAULT TRUE |

Constraints: `UNIQUE(base, quote)`, `CHECK(base != quote)`.

### ref.fx_rates_daily

| Column | Type | Notes |
|--------|------|-------|
| pair_id | INTEGER FK PK | → fx_pairs.id |
| rate_date | DATE PK | |
| rate | NUMERIC(18,8) | 1 base = X quote |
| source | VARCHAR(30) | |

### ref.markets
Seeds: IND (India Equities), USA (US Equities).

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| code | VARCHAR(10) UNIQUE | IND, USA, EUR, GBR, SGP |
| name | VARCHAR(100) | |
| country_code | CHAR(2) FK | → countries.code |
| currency_code | CHAR(3) FK | → currencies.code |

### ref.exchanges
Seeds: NSE, BSE, NYSE, NASDAQ.

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| code | VARCHAR(20) UNIQUE | NSE, BSE, NYSE, NASDAQ |
| name | VARCHAR(200) | |
| market_code | VARCHAR(10) FK | → markets.code |
| country_code | CHAR(2) FK | → countries.code |
| currency_code | CHAR(3) FK | → currencies.code |
| timezone | VARCHAR(50) | IANA timezone |
| open_time | VARCHAR(8) | HH:MM:SS local |
| close_time | VARCHAR(8) | HH:MM:SS local |
| calendar_key | VARCHAR(20) | exchange_calendars lib key |

## Tier 2: Instruments & Contracts (ref)

### ref.instruments
One row per underlying tradeable entity. RELIANCE = 1 row, AAPL = 1 row, NIFTY 50 = 1 row. Derivatives contracts tracked separately in `ref.contracts`.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | gen_random_uuid() |
| exchange_id | INTEGER FK | → exchanges.id |
| instrument_key | VARCHAR(100) UNIQUE | NSE_EQ\|INE002A01018 |
| trading_symbol | VARCHAR(50) | RELIANCE, AAPL |
| name | VARCHAR(200) | |
| isin | VARCHAR(12) | NULL for INDEX |
| segment | VARCHAR(20) | NSE_EQ, NSE_INDEX, US_EQ |
| instrument_type | VARCHAR(10) | EQ, INDEX, ETF |
| asset_class | VARCHAR(20) | equity, index, etf |
| country_code | CHAR(2) FK | → countries.code |
| market_code | VARCHAR(10) FK | → markets.code |
| currency_code | CHAR(3) FK | → currencies.code |
| lot_size | INTEGER | DEFAULT 1 |
| tick_size | NUMERIC(10,2) | |
| freeze_quantity | NUMERIC(12,1) | |
| exchange_token | VARCHAR(20) | Exchange-assigned numeric ID |
| security_type | VARCHAR(20) | NORMAL, etc. |
| sector | VARCHAR(100) | GICS sector or equivalent |
| status | VARCHAR(20) | active, delisted, suspended |
| first_seen | DATE | First ingestion date |
| last_seen | DATE | Most recent instruments master appearance |

Indexes: instrument_key (UNIQUE), trading_symbol, segment, isin, country_code, market_code, status.

### ref.contracts
One row per derivatives contract. RELIANCE FUT APR 26 = 1 row, RELIANCE FUT MAY 26 = 1 row. FK to the underlying instrument.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | gen_random_uuid() |
| instrument_id | UUID FK | → instruments.id (the underlying) |
| exchange_id | INTEGER FK | → exchanges.id |
| contract_key | VARCHAR(100) UNIQUE | NSE_FO\|67003 |
| trading_symbol | VARCHAR(80) | RELIANCE FUT 26 APR 26 |
| contract_type | VARCHAR(10) | FUT, CE, PE |
| segment | VARCHAR(20) | NSE_FO, BSE_FO |
| expiry | DATE NOT NULL | |
| strike_price | NUMERIC(18,2) | 0 for FUT, strike for OPT |
| lot_size | INTEGER NOT NULL | |
| tick_size | NUMERIC(10,2) | |
| freeze_quantity | NUMERIC(12,1) | |
| exchange_token | VARCHAR(20) | |
| weekly | BOOLEAN | DEFAULT FALSE |
| status | VARCHAR(20) | active, expired |
| first_seen | DATE | |
| last_seen | DATE | |

Indexes: contract_key (UNIQUE), instrument_id, expiry, segment, status, (instrument_id, expiry, contract_type).

**Daily lifecycle:** New contracts → INSERT, existing → UPDATE last_seen, expired → UPDATE status='expired'.

### ref.instrument_daily
SCD for mutable instrument fields (lot_size, freeze_qty changes).

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| instrument_id | UUID FK | → instruments.id |
| snapshot_date | DATE | |
| lot_size | INTEGER | |
| freeze_quantity | NUMERIC(12,1) | |
| tick_size | NUMERIC(10,2) | |
| source | VARCHAR(30) | |

Constraint: `UNIQUE(instrument_id, snapshot_date)`.

## Tier 3: Fact Tables (market)

### market.candles_1min — TimescaleDB hypertable
Stores both EQ candles (instrument_id set, contract_id NULL) and FUT candles (both set).

| Column | Type | Notes |
|--------|------|-------|
| instrument_id | UUID FK | → instruments.id (always set) |
| contract_id | UUID FK NULL | → contracts.id (set for FUT/OPT) |
| bar_time | TIMESTAMPTZ | |
| open/high/low/close | NUMERIC(18,6) | |
| volume | BIGINT | |
| oi | BIGINT | Open interest (0 for EQ) |
| source | VARCHAR(20) | upstox, ibkr |
| ingested_at | TIMESTAMPTZ | DEFAULT now() |

Partial unique indexes for dedup:
- `UNIQUE(instrument_id, bar_time) WHERE contract_id IS NULL` — EQ
- `UNIQUE(contract_id, bar_time) WHERE contract_id IS NOT NULL` — FUT

TimescaleDB: chunk_interval = 1 day.

### market.candles_daily — TimescaleDB hypertable
Same pattern as 1min with `trade_date DATE` instead of `bar_time`, plus `adj_close`. TimescaleDB: chunk_interval = 1 month.

### market.raw_responses
Immutable audit log of API responses (JSONB payload).

## Tier 4: Universe Tables (universe)

### universe.indexes
Named universes: nifty50, sp500, fo_eligible. FK to ref.markets.

### universe.members
Point-in-time membership. Query pattern:
```sql
WHERE start_date <= @as_of AND (end_date IS NULL OR end_date > @as_of)
```

## Tier 5: Domain alt_* schemas (country-tagging convention)

When a domain schema (`alt_political`, future `alt_social`, `alt_research`) holds data that is conceptually **per-jurisdiction**, every dim and event row carries `country_code` FK to `ref.countries.code`. v1 seeds with the originating country (e.g. `'US'` for Congress). Later jurisdictions re-use the same tables — different `country_code`, same shape.

### Worked example — `alt_political` (LIVE 2026-04-30, 26 tables)

`alt_political` is the first domain schema using this convention. Source-specific names (`fec_committees`, `lda_issue_codes`, `lda_government_entities`, `asset_type_codes`) are kept since these are US-FEC/LDA/House-Clerk-specific instruments. Future UK/EU/India equivalents land in **sibling tables** with their own names — no over-generalization.

**Country-tagging rules applied:**

```sql
-- (1) Every dim + event has country_code FK to ref.countries.code, default 'US'
country_code  CHAR(2)  NOT NULL  DEFAULT 'US'
  REFERENCES ref.countries(code)

-- (2) Composite PK includes country_code where natural keys could collide
--     across countries (committee_id 'SSFI' is US Senate Finance, but a
--     UK House committee might collide on the same string)
PRIMARY KEY (country_code, committee_id)
PRIMARY KEY (country_code, code)             -- asset_type_codes, lda_issue_codes
PRIMARY KEY (country_code, jacket_number)    -- hearings

-- (3) Where natural key is globally unique by construction (UUIDs, FEC IDs,
--     bioguide IDs), country_code is a regular tagging column not in PK
PRIMARY KEY (bioguide_id)         -- legislators (bioguide IDs are globally unique)
PRIMARY KEY (filing_uuid)         -- lobbying_filings (UUIDs)
PRIMARY KEY (trade_id)            -- legislator_trades (UUID generated)

-- (4) Composite FKs reference composite PKs cleanly
FOREIGN KEY (country_code, committee_id)
  REFERENCES alt_political.committees(country_code, committee_id)

-- (5) Bill identifiers prefix country for human readability
bill_uid = 'US-119-HR-1968'   -- {country}-{congress}-{type}-{number}
```

**Generalization to UK / EU / India later:**

A future UK MP register would add rows to `legislators` with `country_code='GB'`, but their committee data would land in a sibling `parliament_committees` table (UK doesn't have an FEC equivalent; the data shape is different enough to warrant a separate name). LDA-style lobbying disclosure exists in the EU as the EU Transparency Register — same shape as `lobbying_filings` modulo field names, so likely a sibling `eu_lobbying_filings`. The ISO country tag stays consistent throughout.

The convention to follow when adding `alt_social`, `alt_research`, or other domain schemas with per-jurisdiction data:

1. Add `country_code` FK to every dim and event
2. Composite-PK with `country_code` when natural keys could collide
3. Source-specific table names are fine; don't over-generalize. Sibling tables when shapes diverge.
4. Cross-domain joins go via the two stable keys: `bioguide_id` (legislator side) / `ticker` (company side via `ref.instruments.id`).

## Migration Sequence

| # | Migration | Description |
|---|-----------|-------------|
| 001 | create_schemas | 8 Postgres schemas + TimescaleDB extension |
| 002 | create_ref_tables | countries, currencies, fx_pairs, fx_rates_daily, markets, exchanges |
| 003 | create_instruments | instruments, contracts, instrument_daily |
| 004 | create_market_tables | candles_1min (hypertable), candles_daily (hypertable), raw_responses |
| 005 | create_universe_tables | indexes, members |
| 006 | seed_reference_data | Countries, currencies, FX pairs, markets, exchanges |
| 007 | create_alt_political_tables | 26 tables — US Congress trades, lobbying, contracts, donations, bills, hearings (country-tagged) |
| 008 | seed_alt_political_reference | asset_type_codes (48), lda_issue_codes (79), lda_government_entities (257), committee_sector_map (21) — all `country_code='US'` |

## Verification

Run the batch test:
```bash
python scripts/batch_test_db.py
```

This verifies: seed data, instrument/contract inserts, candle writes (EQ + FUT), full join path across all schemas, and TimescaleDB hypertable status.
