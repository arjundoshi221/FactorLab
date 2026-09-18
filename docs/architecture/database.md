# Database — Storage, Hosting, and Live Schema

> Single source of truth for FactorLab's database. Top half: storage strategy + hosting + setup. Bottom half: live schema reference (every table, every column, every FK). When schemas change in code, this file changes too — see `src/factorlab/storage/schemas/` for the SQLAlchemy authoritative definitions.
>
> Last updated: 2026-05-15

---

# Part A — Storage Architecture

## Hybrid: Postgres + DuckDB/Parquet

| Layer | Store | Role |
|-------|-------|------|
| **Hot / mutable** | Postgres + TimescaleDB | System of record. Concurrent writes, FK integrity, point-in-time queries, raw archives, signals, universe membership, experiments. |
| **Cold / immutable** | Parquet files in `data/<source>/` | Finalized daily/minute OHLCV. DuckDB reads directly — zero ETL. |
| **Research queries** | DuckDB | Joins Postgres (`postgres_scanner`) + Parquet in a single query. Best of both for backtests. |

Postgres is canonical — if it isn't in Postgres, it doesn't exist. Parquet is a materialized read replica for analytical workloads. After daily ingestion into Postgres, a nightly export writes finalized bars to Parquet. DuckDB reads Parquet for heavy cross-sectional scans where Postgres I/O becomes the bottleneck.

## Why TimescaleDB

| Feature | Benefit |
|---------|---------|
| **Hypertables** | Auto-partitioning by time — replaces manual `PARTITION BY RANGE` |
| **Compression** | 10-20x on OHLCV data (critical for minute bars — 250M+ rows for India) |
| **Continuous aggregates** | Auto-rollup 1-min → 5-min → 15-min → hourly → daily |
| **Retention policies** | Auto-drop raw minute data older than N years |

Community edition is free. Production on Railway uses `timescale/timescaledb` Docker image.

## When each store wins

| Query pattern | Best store | Why |
|---|---|---|
| "Latest fundamentals for AAPL as of 2024-03-15" | Postgres | Point-in-time, indexed, FK-joined to ref.instruments |
| "Daily returns for all S&P 500 stocks over 5 years" | DuckDB + Parquet | Columnar scan, no server overhead |
| "Bulk ingest 500 tickers × 5 years daily bars" | Postgres via `COPY` | Transactional, 100x faster than INSERT |
| "1-minute bars for 50 NSE stocks, last 3 months" | Postgres (compressed hypertable) | TimescaleDB compression, still mutable window |
| "Backtest cross-sectional momentum on 3000 stocks" | DuckDB joining Parquet + Postgres | Parquet for price data, Postgres for universe/signals |

---

# Part B — Hosting & Setup

## Production (Railway)

- Provision Postgres add-on (TimescaleDB available as Railway template)
- Railway injects `DATABASE_URL` automatically into deployed services
- Connection pooling via PgBouncer when concurrent jobs justify it
- Backups: Railway daily snapshots + weekly `pg_dump` to object storage

## Local development — Docker

```bash
docker compose up -d
alembic upgrade head
```

- Image: `timescale/timescaledb:latest-pg16` exposed on `:5432`
- Volume-mounted data dir (gitignored)
- `.env`: `DATABASE_URL=postgresql+psycopg://factorlab:<pw>@localhost:5432/factorlab`

## Local development — native PostgreSQL on Windows (alternative)

For machines without Docker, the official PostgreSQL Windows installer works. The factorlab dev machine currently runs **PostgreSQL 18** under `C:\Program Files\PostgreSQL\18\`.

| Parameter | Value |
|-----------|-------|
| Host | `localhost` |
| Port | `5432` |
| Superuser | `postgres` |
| Auth | `trust` for local (no password) — see `pg_hba.conf` |
| Data dir | `C:\Program Files\PostgreSQL\18\data\` |
| Locale | `English_United States.1252` |
| Default tz | `Asia/Calcutta` |
| Page checksums | enabled |

Start/stop:

```cmd
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" start  -D "C:\Program Files\PostgreSQL\18\data" -l "C:\Program Files\PostgreSQL\18\data\server.log"
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" stop   -D "C:\Program Files\PostgreSQL\18\data"
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" status -D "C:\Program Files\PostgreSQL\18\data"
```

Auto-start as Windows service (elevated prompt, one-time):

```cmd
"C:\Program Files\PostgreSQL\18\bin\pg_ctl" register -N "postgresql-18" -D "C:\Program Files\PostgreSQL\18\data"
net start postgresql-18
```

Notes:
- `psql.exe` is blocked by Device Guard on the dev machine — use **pgAdmin 4** (bundled) or Python (`psycopg`).
- Switch to password auth: edit `pg_hba.conf` (`trust` → `scram-sha-256`), then `pg_ctl reload`.

## Migrations

- **Alembic** (SQLAlchemy migration tool), in `migrations/versions/`
- One head, no branches in personal use
- Migration files committed; `alembic upgrade head` is idempotent
- Production deploy = run migrations **before** code switchover
- Initial migration (`001`) creates all 8 schemas + enables TimescaleDB extension

## Connection patterns

- App code uses **SQLAlchemy 2.x Core** (not ORM — analytical workloads)
- `psycopg[binary]` driver
- Connection per process, pool size 5 default
- Read-only research connections use a separate role with `SELECT` grants only
- DuckDB uses `postgres_scanner` extension to join Postgres tables with Parquet files

## Bulk ingestion

Use `COPY` for bulk loads — 100x faster than `INSERT` for million-row daily refreshes. Schema design supports this: no serial PKs, UUID defaults are server-side `gen_random_uuid()`.

```python
with conn.cursor() as cur:
    with cur.copy("COPY market.candles_daily FROM STDIN WITH (FORMAT csv)") as copy:
        for row in rows:
            copy.write_row(row)
```

## Stack summary

| Component | Choice |
|-----------|--------|
| Database | PostgreSQL 16+ / TimescaleDB (community) |
| Container (dev) | `timescale/timescaledb:latest-pg16` via Docker Compose |
| Native (dev fallback) | PostgreSQL 18 Windows installer |
| Migrations | Alembic |
| Driver | psycopg[binary] |
| ORM/Core | SQLAlchemy 2.x Core |
| Bulk load | `COPY` via psycopg |
| Analytical | DuckDB + Parquet (`postgres_scanner` for hybrid) |
| DataFrames | Polars / PyArrow for Parquet writes |
| Hosting | Railway (prod), Docker / native Postgres (dev) |
| Backups | Railway snapshots + `pg_dump` nightly; marketdata reconstructible from source |

---

# Part C — Schema Layout

Eight Postgres schemas. Separating by domain keeps grants, backups, and mental models clean.

```
factorlab=# \dn
        public          ← Alembic version table only
        ref             ← reference dimensions: countries, currencies, fx, markets, exchanges, instruments, contracts
        market          ← time-series facts: candles_daily, candles_1min, raw_responses, adjustment_factors, fundamentals
        universe        ← index definitions, point-in-time membership
        alt_political   ← US Congress trades + lobbying + contracts + donations + bills + hearings (26 tables, country-tagged)
        alt_social      ← reddit, twitter posts (planned)
        alt_research    ← arxiv papers (planned)
        derived         ← factors, signals, portfolios (planned)
        experiments     ← research run registry, backtest results (planned)
```

## Standard column pattern (all fact tables)

Column factories live in `src/factorlab/storage/schemas/_columns.py`.

```sql
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
-- <primary datetime>   trade_date / bar_time / event_time / filing_date / ...
-- <domain columns>     table-specific
country_code    char(2) NOT NULL    -- ISO 3166-1
currency_code   char(3) NOT NULL    -- ISO 4217
source          varchar(50) NOT NULL    -- adapter that wrote the row
as_of_time      timestamptz NOT NULL    -- point-in-time: when WE ingested this fact
created_at      timestamptz NOT NULL DEFAULT now()
updated_at      timestamptz NOT NULL DEFAULT now()
```

---

# Part D — Live Schema Reference

> Authoritative tables defined in `src/factorlab/storage/schemas/`. Status tags:
> `[live]` = migrated and ingesting · `[built]` = migrated, no live ingest · `[planned]` = schema stub or design only.

## D.1 — `ref` (reference dimensions) `[live]`

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

### ref.fx_pairs / ref.fx_rates_daily
Full cross-currency pairs (not USD-normalized). Seeds: 10 pairs.

`fx_pairs(id PK, base CHAR(3) FK, quote CHAR(3) FK, pair_code VARCHAR(7) UNIQUE, source, active BOOL)` — constraints: `UNIQUE(base,quote)`, `CHECK(base != quote)`.

`fx_rates_daily(pair_id PK FK, rate_date PK, rate NUMERIC(18,8), source)`.

### ref.markets
Seeds: IND (India), USA (US).

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

### ref.instruments
One row per underlying tradeable entity. RELIANCE = 1 row, AAPL = 1 row, NIFTY 50 = 1 row. Derivatives sit in `ref.contracts`.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | gen_random_uuid() |
| exchange_id | INTEGER FK | → exchanges.id |
| instrument_key | VARCHAR(100) UNIQUE | `NSE_EQ\|INE002A01018` |
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
| exchange_token | VARCHAR(20) | exchange-assigned numeric ID |
| security_type | VARCHAR(20) | NORMAL, etc. |
| sector | VARCHAR(100) | GICS sector or equivalent |
| status | VARCHAR(20) | active, delisted, suspended |
| first_seen | DATE | first ingestion date |
| last_seen | DATE | most recent instruments master appearance |

Indexes: `instrument_key UNIQUE`, `trading_symbol`, `segment`, `isin`, `country_code`, `market_code`, `status`.

Survivorship-bias rule: delisted instruments stay with full history — never delete.

### ref.contracts
One row per derivatives contract. FK to the underlying instrument.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | gen_random_uuid() |
| instrument_id | UUID FK | → instruments.id (underlying) |
| exchange_id | INTEGER FK | → exchanges.id |
| contract_key | VARCHAR(100) UNIQUE | `NSE_FO\|67003` |
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
| first_seen / last_seen | DATE | |

Indexes: `contract_key UNIQUE`, `instrument_id`, `expiry`, `segment`, `status`, `(instrument_id, expiry, contract_type)`.

Daily lifecycle: new → INSERT, existing → UPDATE last_seen, expired → UPDATE status.

### ref.instrument_daily
SCD for mutable instrument fields (lot_size, freeze_quantity changes).

`(id SERIAL PK, instrument_id UUID FK, snapshot_date DATE, lot_size, freeze_quantity, tick_size, source)` — `UNIQUE(instrument_id, snapshot_date)`.

---

## D.2 — `market` (time-series facts) `[live]`

### market.candles_daily — TimescaleDB hypertable
Daily OHLCV. Stores EQ (instrument_id only) and FUT (instrument_id + contract_id) bars.

| Column | Type | Notes |
|--------|------|-------|
| instrument_id | UUID FK | → instruments.id (always set) |
| contract_id | UUID FK NULL | → contracts.id (set for FUT/OPT) |
| trade_date | DATE | |
| open / high / low / close | NUMERIC(18,6) | RAW (unadjusted) |
| adj_close | NUMERIC(18,6) | adjusted — vendors rewrite retroactively |
| volume | BIGINT | |
| oi | BIGINT | open interest (0 for EQ) |
| source | VARCHAR(20) | eodhd, schwab, upstox, ibkr |
| ingested_at | TIMESTAMPTZ | DEFAULT now() |

Partial unique indexes for dedup:
- `UNIQUE(instrument_id, trade_date) WHERE contract_id IS NULL` — EQ
- `UNIQUE(contract_id, trade_date) WHERE contract_id IS NOT NULL` — FUT

TimescaleDB chunk_interval = 1 month. Always store both `close` (raw) and `adj_close`. Never overwrite raw.

### market.candles_1min — TimescaleDB hypertable
Same shape as `candles_daily` with `bar_time TIMESTAMPTZ` instead of `trade_date`. Used for intraday (Schwab US, Upstox IN). TimescaleDB chunk_interval = 1 day.

### market.candles_intraday — session-tagged intraday `[built]`
Single table for US intraday with session enum (`pre`, `regular`, `post`) — chosen over multi-table split (see migration 011). Pre/regular/post US sessions: `04:00 / 09:30 / 16:00 / 20:00` ET. Schwab retail pre-market floor: `07:00`. Bars duplicated 2× in raw API responses — dedup at ingest.

### market.adjustment_factors — split/dividend factor history `[built]`
Reconstructs what adjusted prices looked like on any past date. Vendors silently rewrite `adj_close` retroactively after every action.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| instrument_id | UUID FK | → ref.instruments |
| ex_date | DATE NOT NULL | |
| factor_type | VARCHAR(20) | split, dividend, rights, spinoff |
| factor | NUMERIC(18,10) NOT NULL | ratio: 2.0 for 2-for-1 split |
| cumulative_factor | NUMERIC(18,10) NOT NULL | running product |
| country_code, currency_code | | |
| source, as_of_time | | |

### market.fundamentals — tritemporal `[planned]`
Period_end + filing_date + as_of_time. Multiple rows per (instrument, period_end, metric) capture restatements. Backtest query: `WHERE filing_date <= @as_of AND as_of_time <= @ingest_cutoff`.

### market.raw_responses
Immutable audit log of API responses (JSONB payload).

---

## D.3 — `universe` (index definitions & membership) `[live]`

### universe.indexes
Named universes: `nifty50`, `sp500`, `fo_eligible`, `russell3000`. FK to `ref.markets`.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| name | VARCHAR(100) UNIQUE | "S&P 500", "Russell 3000", "NIFTY 50" |
| description | TEXT | |
| market_code | VARCHAR(10) FK | |
| index_type | VARCHAR(30) | benchmark, custom, sector, factor |

### universe.members — point-in-time membership

`(id UUID PK, index_id UUID FK, instrument_id UUID FK, start_date DATE, end_date DATE NULL)`.

Never ask "is AAPL in S&P 500?" — ask "was AAPL in S&P 500 on 2024-03-15?":

```sql
WHERE start_date <= @as_of AND (end_date IS NULL OR end_date > @as_of)
```

---

## D.4 — `alt_political` (US Congress alt-data) `[live]`

26 tables, country-tagged. Built in migrations 007/008/009, evolved through 028 (dedup view). **Always query the `legislator_trades_dedup` view for analytics, not the raw table** — see [`data-sources/political/pipeline.md`](../data-sources/political/pipeline.md).

**Two stable join keys:**
- `bioguide_id` — legislator side
- `ticker` — company side (denormalized into events; ground truth via `instrument_id → ref.instruments.id`)

**Country-tagging convention:**

```sql
-- Every dim + event has country_code FK to ref.countries.code, default 'US'
country_code  CHAR(2)  NOT NULL  DEFAULT 'US'
  REFERENCES ref.countries(code)

-- Composite PK includes country_code where natural keys could collide
PRIMARY KEY (country_code, committee_id)
PRIMARY KEY (country_code, code)             -- asset_type_codes, lda_issue_codes
PRIMARY KEY (country_code, jacket_number)    -- hearings

-- Where natural key is globally unique (UUIDs, FEC IDs, bioguide IDs), country_code is tagging only
PRIMARY KEY (bioguide_id)         -- legislators
PRIMARY KEY (filing_uuid)         -- lobbying_filings
PRIMARY KEY (trade_id)            -- legislator_trades

-- Bill identifiers prefix country for human readability
bill_uid = 'US-119-HR-1968'   -- {country}-{congress}-{type}-{number}
```

### Dimensions (12 tables)

| Table | PK | Source |
|---|---|---|
| `legislators` | `bioguide_id` | unitedstates/congress-legislators YAML |
| `legislator_terms` | `(bioguide_id, term_start)` | YAML terms array |
| `legislator_fec_ids` | `fec_candidate_id` | YAML + FEC |
| `committees` | `(country_code, committee_id)` | YAML; subcommittees self-FK to parent |
| `committee_assignments` | `(country_code, committee_id, bioguide_id, congress_number)` | YAML; SCD `valid_from/to` |
| `committee_sector_map` | `(country_code, committee_id, gics_code)` | Manual seed |
| `asset_type_codes` | `(country_code, code)` | fd.house.gov 48 codes |
| `lda_issue_codes` | `(country_code, code)` | LDA constants — 79 codes |
| `lda_government_entities` | `(country_code, entity_id)` | LDA constants — 257 entities |
| `fec_committees` | `(country_code, committee_id)` | FEC `/committees/` |
| `contract_aliases` | `(country_code, normalized_name)` | resolver: USASpending recipient → ticker |
| `lobby_client_aliases` | `(country_code, normalized_name)` | resolver: LDA client → ticker |

### Trade events (1 table + 1 view)

| Surface | Use for | Why |
|---|---|---|
| `legislator_trades` (raw table) | Audit trail, provenance, ingest idempotency, schema work | Append-only multi-source. Contains logical duplicates across overlapping sources. PK `trade_id` (UUID). UNIQUE dedup on `(country_code, chamber, filing_id, transaction_date, asset_name_raw, transaction_type, amount_str)`. |
| `legislator_trades_dedup` (view) | **All analytics, factor construction, signal queries** | Source-deduped. One row per logical trade. Adds `source_code`. Precedence: eFD > paper-LLM > SSW historical. Same-source PTR amendments collapsed. NULL-key rows pass through. Non-materialized — no `REFRESH` step. Migration 028. |

### Conjunction-layer events (12 tables)

| Table | PK |
|---|---|
| `gov_contracts` | `contract_id` (USASpending generated_internal_id) |
| `lobbying_filings` | `filing_uuid` |
| `lobbying_activities` | `activity_id` (UUID) |
| `lobbying_activity_targets` | `(activity_id, country_code, entity_id)` |
| `lobbying_activity_lobbyists` | `(activity_id, lobbyist_id, last_name, first_name)` |
| `campaign_donations` | `donation_id` (UUID) |
| `bills` | `bill_uid` (`{country}-{congress}-{type}-{number}`) |
| `bill_sponsors` | `(bill_uid, bioguide_id, role)` |
| `bill_committees` | `(bill_uid, country_code, committee_id, activity_date, activity_type)` |
| `bill_actions` | `action_id` (UUID) |
| `hearings` | `(country_code, jacket_number)` |
| `hearing_witnesses` | `(country_code, jacket_number, witness_seq)` |

### Audit (1 table)
`raw_archive` — gzipped response bytes per fetch, source-tagged, source-url-indexed for replay. Consolidates audit for all 8 political sources (House Clerk, Senate eFD, LDA, USASpending, Finnhub, FEC, Congress.gov, SEC).

### Conceptual ER — the two join surfaces

```
                       LEGISLATOR SIDE                         COMPANY SIDE
                       (bioguide_id)                           (ticker)
                            │                                    │
              ┌─────────────┼──────────────┐         ┌───────────┴──────────┐
              │             │              │         │                      │
        legislators  legislator_terms  legislator_  ref.instruments     gov_contracts
              │       (chamber/state/    fec_ids        │                   │
              │        district SCD)        │           │                   │
              │             │               │           └─ legislator_      │
       committee_assignments               (FEC          trades.instrument_id│
              │  (per Congress, role)       link)              │            │
              │             │                                  │       lobbying_filings.
        committees ←────────┘                                  ↑       client_ticker
              │                                          ticker is                │
       committee_sector_map                              denormalized       lobbying_
              │                                          into events         activities
              │
        bill_committees
              │
        bills ← bill_sponsors
              │
        bill_actions

        campaign_donations  ← bridges both: donor_committee_id (FEC PAC) on the company
                              side via fec_committees.sponsor_company_ticker;
                              candidate_id (legislator_fec_ids) on the legislator side.
```

### Indexes optimized for common queries

| Query path | Index |
|---|---|
| Recent trades by member X | `legislator_trades(bioguide_id, transaction_date)` |
| Who traded ticker T | `legislator_trades(ticker, transaction_date)` |
| Contract pipeline for ticker T | `gov_contracts(ticker, action_date DESC)` |
| Lobby spend trend for ticker T | `lobbying_filings(client_ticker, filing_year)` |
| Donations to senator X's PAC | `campaign_donations(recipient_committee_id, date DESC)` |
| Employees of X giving in cycle Y | `campaign_donations(donor_employer, cycle)` |
| Member X's committees | `committee_assignments(bioguide_id)` |
| Bills before committee C | `bill_committees(committee_id, activity_date DESC)` |

### Edge cases handled

- **Retired senators** — `legislator_trades.bioguide_id` nullable; preserves `legislator_name_raw` for re-resolution after `legislators-historical.yaml` ingestion.
- **Senate Stock Watcher 2014-2019 backfill** — `filing_id` accepts a composite hash since SSW lacks DocIDs.
- **Paper-filed Senate PTRs (GIF scans)** — viewer HTML + image URLs go to `raw_archive` only; OCR pipeline deferred.
- **Subsidiary contracts** — both `recipient_legal_name` and `recipient_parent_name` stored; resolver writes to `contract_aliases`.
- **Finnhub vs USASpending** — `gov_contracts.source` distinguishes; both can coexist for the same logical award.
- **Bill ID stability** — `bill_uid` deterministic across re-fetches; UPSERT pattern.
- **Lobbyist identity** — composite PK includes name fields since LDA's `lobbyist_id` may be missing.

---

## D.5 — Future schemas `[planned]`

- `alt_social` — Reddit, Twitter posts. Same country-tagging convention.
- `alt_research` — arXiv papers. Same convention.
- `derived` — factors, signals, portfolios, sectoral political-alpha rollups (GICS-level). Builds on top of `alt_*` raw facts.
- `experiments` — `runs(run_id PK, code_sha, config_hash, config JSONB, notes, result_uri)` + backtest metrics.

---

# Part E — Conventions

## Country-tagging — convention for new domain schemas

When adding `alt_social`, `alt_research`, or other per-jurisdiction domain schemas:

1. Add `country_code` FK to every dim and event
2. Composite-PK with `country_code` when natural keys could collide across jurisdictions
3. Source-specific table names are fine; don't over-generalize. Sibling tables when shapes diverge (e.g., a future UK MP register lives in `parliament_committees`, not `committees`).
4. Cross-domain joins go via the two stable keys: `bioguide_id` (legislator side) / `ticker` (company side via `ref.instruments.id`).

## Raw-archive policy

Every adapter writes its raw response to either a domain-specific `raw_*` table or the consolidated `<domain>.raw_archive`. **Never deleted.** Parsers re-run against raw if schemas change.

```sql
CREATE TABLE market.raw_eodhd (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    endpoint      varchar(200) NOT NULL,
    source_url    text NOT NULL,
    payload       jsonb NOT NULL,
    parsed_into   varchar(100)
);
```

## Indexing strategy

Optimized for time-series read patterns:

| Index pattern | Purpose | Used by |
|---|---|---|
| `(instrument_id, trade_date DESC)` | "AAPL daily bars, newest first" | candles_daily |
| `(instrument_id, as_of_time DESC)` | "Latest known version of this fact" | all fact tables |
| `(trade_date)` | Cross-sectional: "all stocks on 2024-03-15" | candles_daily |
| `(index_id, start_date, end_date)` | "Who was in S&P 500 on date X?" | universe.members |
| `(vendor, vendor_id)` | resolve vendor symbol to internal UUID | aliases |

Rules:
- Max 5 indexes per table — write cost adds up
- TimescaleDB auto-indexes hypertable time column
- Composite indexes go `(id_col, time_col DESC)` — newest-first is the common query

---

# Part F — Migration Sequence

| # | Migration | Description |
|---|-----------|-------------|
| 001 | `create_schemas` | 8 Postgres schemas + TimescaleDB extension |
| 002 | `create_ref_tables` | countries, currencies, fx_pairs, fx_rates_daily, markets, exchanges |
| 003 | `create_instruments` | instruments, contracts, instrument_daily |
| 004 | `create_market_tables` | candles_1min (hypertable), candles_daily (hypertable), raw_responses |
| 005 | `create_universe_tables` | indexes, members |
| 006 | `seed_reference_data` | Countries, currencies, FX pairs, markets, exchanges |
| 007 | `create_alt_political_tables` | 26 tables — US Congress trades, lobbying, contracts, donations, bills, hearings (country-tagged) |
| 008 | `seed_alt_political_reference` | asset_type_codes (48), lda_issue_codes (79), lda_government_entities (257), committee_sector_map (21) — all `country_code='US'` |
| 011 | `add_intraday_session_and_5min` | `market.candles_intraday` with session enum + 5-min hypertable |
| 012–027 | per-country / BCNF redesign | per-country schema split, BCNF normalization, vendor-keyed aliases (2026-05-01 redesign) |
| 028 | `legislator_trades_dedup_view` | Cross-source dedup view: eFD > paper-LLM > SSW historical; PTR amendments collapsed |

For full migration history: `migrations/versions/`. For the 2026-05-01 redesign rationale, see memory entry `project_redesign_2026_05_01.md`.

---

# Part G — Verification

```bash
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/_shared/check_db.py
```

Verifies: seed data, instrument/contract inserts, candle writes (EQ + FUT), full join path across all schemas, and TimescaleDB hypertable status.

---

# Part H — Open questions

- [ ] Single Railway DB instance for dev + prod, or separate? (separate — never share)
- [ ] Time-travel needs: `pg_dump`-based snapshots or table-level versioning (`temporal_tables`)?
- [ ] Parquet export cadence: nightly batch or on-demand? (likely nightly for finalized bars)
- [ ] TimescaleDB compression policy: compress after 7 days for minute bars? Tuning needed.
- [ ] DuckDB `postgres_scanner` vs `pg_parquet` for the hybrid query path?
