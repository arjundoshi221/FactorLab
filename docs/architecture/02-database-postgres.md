# Database — PostgreSQL + TimescaleDB (Hybrid with DuckDB/Parquet)

> Status: `[scaffold]`

---

## Storage strategy

### Hybrid architecture

| Layer | Store | Role |
|-------|-------|------|
| **Hot / mutable** | Postgres + TimescaleDB | System of record. Concurrent writes, FK integrity, point-in-time queries, raw archives, signals, universe membership, experiments. |
| **Cold / immutable** | Parquet files in `data/<source>/` | Finalized daily/minute OHLCV. DuckDB reads directly — zero ETL. |
| **Research queries** | DuckDB | Joins Postgres (`postgres_scanner`) + Parquet in a single query. Best of both for backtests. |

Postgres is canonical — if it isn't in Postgres, it doesn't exist. Parquet is a materialized read replica for analytical workloads. After daily ingestion into Postgres, a nightly export writes finalized bars to Parquet. DuckDB reads Parquet for heavy cross-sectional scans where Postgres I/O becomes the bottleneck.

### Why TimescaleDB

| Feature | Benefit |
|---------|---------|
| **Hypertables** | Auto-partitioning by time — replaces manual `PARTITION BY RANGE` |
| **Compression** | 10-20x on OHLCV data (critical for minute bars — 250M+ rows for India) |
| **Continuous aggregates** | Auto-rollup 1-min → 5-min → 15-min → hourly → daily |
| **Retention policies** | Auto-drop raw minute data older than N years |

Community edition is free. Production on Railway uses `timescale/timescaledb` Docker image.

### When each store wins

| Query pattern | Best store | Why |
|---|---|---|
| "Latest fundamentals for AAPL as of 2024-03-15" | Postgres | Point-in-time, indexed, FK-joined to ref.securities |
| "Daily returns for all S&P 500 stocks over 5 years" | DuckDB + Parquet | Columnar scan, no server overhead |
| "Bulk ingest 500 tickers × 5 years daily bars" | Postgres via `COPY` | Transactional, 100x faster than INSERT |
| "1-minute bars for 50 NSE stocks, last 3 months" | Postgres (compressed hypertable) | TimescaleDB compression, still mutable window |
| "Backtest cross-sectional momentum on 3000 stocks" | DuckDB joining Parquet + Postgres | Parquet for price data, Postgres for universe/signals |

---

## Hosting plan

### Local development
- Docker Compose: `timescale/timescaledb:latest-pg16` exposed on `:5432`
- Volume-mounted data dir (gitignored)
- `.env`: `DATABASE_URL=postgresql+psycopg://factorlab:<pw>@localhost:5432/factorlab`

```bash
docker compose up -d
alembic upgrade head
```

### Production: Railway
- Provision Postgres add-on (TimescaleDB available as Railway template)
- Railway injects `DATABASE_URL` automatically into deployed services
- Connection pooling via PgBouncer if/when concurrent jobs justify it
- Backups: Railway daily snapshots + weekly `pg_dump` to object storage

---

## Schema strategy

### Schemas (namespaces)

```
factorlab=# \dn
        public          ← Alembic version table only
        ref             ← reference data: securities, aliases, exchanges, calendars
        market          ← prices, fundamentals, corporate actions, adjustment factors, raw archives, IBKR mirrors
        universe        ← index definitions, point-in-time membership
        alt_social      ← reddit, twitter posts
        alt_political   ← senator trades, lobbying, etc.
        alt_research    ← arxiv papers
        derived         ← factors, signals, portfolios
        experiments     ← research run registry, backtest results
```

8 domain schemas. Separating by domain keeps grants, backups, and mental models clean.

### Standard column pattern (all fact tables)

Every fact table carries these columns:

```sql
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
-- <primary datetime>   trade_date / bar_time / event_time / filing_date / ...
-- <domain columns>     table-specific
market          varchar(10) NOT NULL    -- 'us', 'in', 'eu', 'gb'
currency        char(3) NOT NULL        -- ISO 4217
source          varchar(50) NOT NULL    -- adapter that wrote the row
as_of_time      timestamptz NOT NULL    -- point-in-time: when WE ingested this fact
created_at      timestamptz NOT NULL DEFAULT now()
updated_at      timestamptz NOT NULL DEFAULT now()  -- trigger-updated on modify
```

Column factories in `src/factorlab/storage/schemas/_columns.py`.

### Core tables

**`ref.securities`** — canonical security identity (stable UUID per security)
```sql
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
name            text NOT NULL
country         char(2) NOT NULL              -- ISO 3166
asset_class     varchar(20) NOT NULL          -- 'equity', 'future', 'index', 'etf'
sector          varchar(100)                  -- GICS sector or equivalent
status          varchar(20) NOT NULL DEFAULT 'active'  -- 'active', 'delisted', 'merged', 'suspended'
market          varchar(10) NOT NULL
currency        char(3) NOT NULL
created_at      timestamptz NOT NULL DEFAULT now()
updated_at      timestamptz NOT NULL DEFAULT now()
```
Delisted tickers stay with full history — never delete. Survivorship bias is the #1 way personal platforms silently lie.

**`ref.security_aliases`** — vendor-specific ID mapping
```sql
security_id     uuid REFERENCES ref.securities
vendor          varchar(30) NOT NULL          -- 'eodhd', 'upstox', 'ibkr'
vendor_id       varchar(100) NOT NULL         -- vendor-native identifier
valid_from      date NOT NULL
valid_to        date                          -- null = current
```
Ticker renames/mergers insert a new row, never update the existing one.

**`market.price_bars_daily`** — TimescaleDB hypertable
```sql
id              uuid DEFAULT gen_random_uuid()
security_id     uuid NOT NULL REFERENCES ref.securities
trade_date      date NOT NULL
open            numeric(18,6)
high            numeric(18,6)
low             numeric(18,6)
close           numeric(18,6)                 -- RAW (unadjusted)
adj_close       numeric(18,6)                 -- adjusted — vendor rewrites retroactively
volume          bigint
market          varchar(10) NOT NULL
currency        char(3) NOT NULL
source          text NOT NULL
as_of_time      timestamptz NOT NULL
created_at      timestamptz NOT NULL DEFAULT now()
updated_at      timestamptz NOT NULL DEFAULT now()
UNIQUE (security_id, trade_date)
```
Converted to TimescaleDB hypertable on `trade_date`. Always store both `close` (raw) and `adj_close`. Never overwrite raw.

**`market.adjustment_factors`** — split/dividend factor history
```sql
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
security_id     uuid NOT NULL REFERENCES ref.securities
ex_date         date NOT NULL
factor_type     varchar(20) NOT NULL          -- 'split', 'dividend', 'rights', 'spinoff'
factor          numeric(18,10) NOT NULL       -- ratio: 2.0 for 2-for-1 split
cumulative_factor numeric(18,10) NOT NULL     -- running product of all factors
market          varchar(10) NOT NULL
currency        char(3) NOT NULL
source          text NOT NULL
as_of_time      timestamptz NOT NULL
```
Reconstructs what adjusted prices LOOKED LIKE on any past date. Vendors silently rewrite `adj_close` retroactively after every action.

**`market.fundamentals`** — point-in-time (tritemporal: period_end + filing_date + as_of_time)
```sql
id              uuid DEFAULT gen_random_uuid()
security_id     uuid NOT NULL
period_end      date NOT NULL                 -- the quarter/year being reported
filing_date     date NOT NULL                 -- KNOWLEDGE DATE: when filed, publicly knowable
metric          varchar(80) NOT NULL          -- 'revenue', 'net_income', etc.
value           numeric(28,6)
market          varchar(10) NOT NULL
currency        char(3) NOT NULL
source          text NOT NULL
as_of_time      timestamptz NOT NULL          -- when WE ingested this version
UNIQUE (security_id, period_end, metric, as_of_time)
```
Multiple rows per (security, period_end, metric) capture restatements. Backtest queries: `WHERE filing_date <= @as_of AND as_of_time <= @ingest_cutoff`.

**`universe.indexes`** — named universes
```sql
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
name            varchar(100) NOT NULL UNIQUE  -- 'S&P 500', 'Russell 3000', 'NIFTY 50'
description     text
market          varchar(10) NOT NULL
index_type      varchar(30) NOT NULL DEFAULT 'benchmark'  -- benchmark, custom, sector, factor
```

**`universe.membership`** — point-in-time index membership
```sql
id              uuid PRIMARY KEY DEFAULT gen_random_uuid()
index_id        uuid NOT NULL REFERENCES universe.indexes
security_id     uuid NOT NULL REFERENCES ref.securities
start_date      date NOT NULL
end_date        date                          -- null = current member
```
Never ask "is AAPL in S&P 500?" — ask "was AAPL in S&P 500 on 2024-03-15?" Query: `WHERE start_date <= @as_of AND (end_date IS NULL OR end_date > @as_of)`.

**`experiments.runs`** — every research/backtest invocation
```sql
run_id          uuid PRIMARY KEY
created_at      timestamptz NOT NULL
code_sha        text NOT NULL
config_hash     text NOT NULL
config          jsonb NOT NULL
notes           text
result_uri      text
```

### Raw archive

Each adapter writes to a `raw_<source>` table. Never deleted. Parsers re-run against raw if schemas change.

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

---

## Indexing strategy

Optimized for time-series read patterns:

| Index pattern | Purpose | Used by |
|---|---|---|
| `(security_id, trade_date DESC)` | "AAPL daily bars, newest first" | price_bars_daily |
| `(security_id, as_of_time DESC)` | "Latest known version of this fact" | all fact tables |
| `(trade_date)` | Cross-sectional: "all stocks on 2024-03-15" | price_bars_daily |
| `(index_id, start_date, end_date)` | "Who was in S&P 500 on date X?" | universe.membership |
| `(vendor, vendor_id)` | Resolve vendor symbol to internal UUID | security_aliases |

Rules:
- Max 5 indexes per table — write cost adds up
- TimescaleDB auto-indexes hypertable time column
- Composite indexes go `(id_col, time_col DESC)` — newest-first is the common query

---

## Bulk ingestion

Use `COPY` for bulk loads — 100x faster than `INSERT` for million-row daily refreshes. Schema design supports this: no serial PKs, UUID defaults are server-side `gen_random_uuid()`.

```python
# psycopg COPY example
with conn.cursor() as cur:
    with cur.copy("COPY market.price_bars_daily FROM STDIN WITH (FORMAT csv)") as copy:
        for row in rows:
            copy.write_row(row)
```

---

## Migrations

- **Alembic** (SQLAlchemy migration tool)
- One head, no branches in personal use
- Migration files committed; `alembic upgrade head` is idempotent
- Production deploy = run migrations *before* code switchover
- Initial migration (`001`) creates all 8 schemas + enables TimescaleDB extension

---

## Connection patterns

- App code uses `SQLAlchemy 2.x` Core (not ORM — analytical workloads)
- `psycopg[binary]` driver
- Connection per process, pool size 5 default
- Read-only research connections use a separate role with `SELECT` grants only
- DuckDB uses `postgres_scanner` extension to join Postgres tables with Parquet files

---

## Stack summary

| Component | Choice |
|-----------|--------|
| Database | PostgreSQL 16 + TimescaleDB (community) |
| Container | `timescale/timescaledb:latest-pg16` via Docker Compose |
| Migrations | Alembic |
| Driver | psycopg[binary] |
| ORM/Core | SQLAlchemy 2.x Core |
| Bulk load | `COPY` via psycopg |
| Analytical | DuckDB + Parquet (`postgres_scanner` for hybrid) |
| DataFrames | Polars / PyArrow for Parquet writes |
| Hosting | Railway (prod), Docker (dev) |
| Backups | Railway snapshots + `pg_dump` nightly; marketdata reconstructible from source |

---

## Open questions

- [ ] Single Railway DB instance for dev + prod, or separate? (separate — never share)
- [ ] Time-travel needs: `pg_dump`-based snapshots or table-level versioning (`temporal_tables`)?
- [ ] Parquet export cadence: nightly batch or on-demand? (likely nightly for finalized bars)
- [ ] TimescaleDB compression policy: compress after 7 days for minute bars? Tuning needed.
- [ ] DuckDB `postgres_scanner` vs `pg_parquet` for the hybrid query path?
