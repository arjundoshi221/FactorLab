# Database - ClickHouse Single-Store Architecture

> Status: `[design]`

---

## Decision

FactorLab will use ClickHouse as the single database for everything:

- market data
- alternative data
- reference data
- derived signals
- experiment metadata
- raw payload retention
- API-facing denormalized serving tables

Postgres, TimescaleDB, and hybrid Postgres/Parquet assumptions are no longer the target architecture.

---

## Why this direction

The system now optimizes for:

- one operational store instead of a split stack
- append-heavy ingestion across market and political data
- denormalized read patterns for factor research and REST APIs
- long-term raw retention inside the primary system
- simpler deployment on one VPS with one backup surface

The cost is weaker relational enforcement. We accept that and move integrity checks into ingestion code, validation queries, and periodic audits.

---

## Core principles

### 1. One write path, two storage layers

Every source writes into:

1. a raw immutable archive table
2. one or more curated serving tables

Raw tables are the replay surface. Curated tables are the query surface.

### 2. Denormalize for reads

Curated tables should be optimized for the questions we actually ask:

- "show all trades on ticker X"
- "which committees matter for this company"
- "what filings changed this week"
- "return daily bars for this universe and date range"

If duplication makes those queries simpler and faster, duplication is acceptable.

### 3. Preserve point-in-time semantics

Even in a denormalized model, facts must keep:

- `event_time` or domain equivalent
- `as_of_time`
- `ingested_at`
- `source`

Backtests and downstream signals must filter on what was knowable at the time.

### 4. Raw retention is permanent

Raw responses are retained long term in ClickHouse. Parser bugs, source drift, and schema redesigns must be recoverable from stored payloads.

### 5. REST-first access

The primary consumer interface is a FastAPI service exposing stable read endpoints. Direct SQL remains available for research, but API-facing serving tables are first-class design targets.

---

## High-level architecture

```text
external source
    |
    v
adapter/fetcher
    |
    v
raw_<source> tables in ClickHouse
    |
    v
parser/normalizer
    |
    v
curated domain tables in ClickHouse
    |
    +--> materialized views / aggregate tables
    |
    +--> FastAPI read endpoints
    |
    +--> research queries / factor jobs
```

---

## Logical domains

Use one physical ClickHouse database with domain-prefixed tables:

- `ref_*`
- `market_*`
- `universe_*`
- `alt_social_*`
- `alt_political_*`
- `alt_research_*`
- `derived_*`
- `experiments_*`

This keeps the deployment simple while preserving domain boundaries in naming.

---

## Table classes

### Raw archive tables

Pattern:

```text
raw_id
source
source_url
fetch_key
status_code
response_headers
response_body
content_encoding
fetched_at
as_of_time
metadata_json
```

Notes:

- store raw body compressed when practical
- partition by fetch month
- order by `(source, fetched_at, raw_id)`
- never mutate except rare repair workflows

### Reference tables

Examples:

- `ref_countries`
- `ref_markets`
- `ref_exchanges`
- `ref_instruments`
- `ref_contracts`
- `ref_aliases`

Notes:

- use replacing/versioned patterns where records can change
- keep stable internal ids even when vendor identifiers change
- treat these as application-maintained dimensions, not FK-enforced truth

### Fact tables

Examples:

- `market_candles_1min`
- `market_candles_daily`
- `market_fundamentals`
- `market_corporate_actions`
- `alt_political_trades`
- `alt_political_contracts`
- `alt_political_lobbying`
- `alt_political_donations`
- `alt_political_bills`
- `alt_political_hearings`

### Serving tables and materialized views

Examples:

- `api_company_political_timeline`
- `api_legislator_activity`
- `api_committee_company_exposure`
- `api_universe_daily_snapshot`
- `api_latest_signal_values`

These are explicitly denormalized and optimized for endpoint latency.

---

## Market data model

### `market_candles_1min`

Core columns:

- `instrument_id`
- `contract_id`
- `symbol`
- `market_code`
- `bar_time`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `oi`
- `source`
- `as_of_time`
- `ingested_at`

Engine guidance:

- `MergeTree`
- partition by `toYYYYMM(bar_time)`
- order by `(instrument_id, bar_time, source)`

### `market_candles_daily`

Core columns:

- `instrument_id`
- `contract_id`
- `symbol`
- `trade_date`
- `open`
- `high`
- `low`
- `close`
- `adj_close`
- `volume`
- `source`
- `as_of_time`
- `ingested_at`

Engine guidance:

- partition by `toYYYYMM(trade_date)`
- order by `(instrument_id, trade_date, source)`

### `market_fundamentals`

Store one row per metric version:

- `instrument_id`
- `period_end`
- `filing_date`
- `metric`
- `value`
- `source`
- `as_of_time`
- `ingested_at`

This preserves restatements and point-in-time backtestability.

---

## `alt_political` redesign

The current SQLAlchemy schema is highly normalized. The new ClickHouse target should collapse it into query-oriented tables.

### Main serving tables

#### `alt_political_trades`

One row per reported transaction with duplicated legislator and company attributes:

- legislator ids and display name
- chamber, state, party
- ticker, instrument id, issuer name
- transaction metadata
- filing metadata
- amount bucket and parsed min/max values
- raw archive pointer

#### `alt_political_contracts`

One row per award or award event:

- recipient identity fields
- resolved ticker and instrument id
- parent company fields
- agency fields
- amounts
- dates
- NAICS and geography
- source metadata
- raw archive pointer

#### `alt_political_lobbying`

One row per filing activity row, not one row per filing plus bridge tables:

- filing identifiers
- client identity and resolved ticker
- registrant identity
- issue code and description
- target entities as arrays
- lobbyists as arrays or nested JSON
- filing dates and amounts
- raw archive pointer

#### `alt_political_donations`

One row per contribution:

- donor identity
- donor employer
- donor committee fields
- recipient committee fields
- candidate fields
- resolved sponsor company fields where applicable
- cycle, date, amount
- raw archive pointer

#### `alt_political_bills`

One row per bill version with embedded arrays for:

- sponsors
- cosponsors
- committees
- latest actions
- policy area tags
- related tickers or sectors when derived

#### `alt_political_hearings`

One row per hearing with:

- committee identity
- date
- title
- witness arrays
- linked bills if present
- raw archive pointer

### Supporting dimensions that still make sense

Keep a small number of reusable entity tables where they provide lookup value:

- `ref_instruments`
- `alt_political_legislators`
- `alt_political_committees`
- `alt_political_fec_committees`
- alias resolution tables for client and contractor names

Those are support tables, not the main query path.

---

## API design implications

REST endpoints should map to serving tables, not normalized join graphs.

Examples:

- `GET /api/v1/companies/{ticker}/political/timeline`
- `GET /api/v1/companies/{ticker}/contracts`
- `GET /api/v1/companies/{ticker}/lobbying`
- `GET /api/v1/legislators/{bioguide_id}/trades`
- `GET /api/v1/committees/{committee_id}/activity`
- `GET /api/v1/market/candles/daily`
- `GET /api/v1/universes/{name}/members`

---

## Ingestion and correction model

### Idempotency

Every pipeline needs a deterministic dedup key:

- source-native id when available
- otherwise a content hash of source identity fields

### Corrections

Use one of two patterns per table:

- append a new version row and resolve latest in views
- use replacement semantics with a monotonic version column

Default preference:

- market bars: replacement semantics
- filings and event data: append versions where corrections matter historically

### Validation

Because ClickHouse will not enforce relational correctness the way Postgres would, add validation jobs for:

- orphan instrument ids
- duplicate business keys
- impossible dates
- broken resolution rates
- null-rate drift on key fields

---

## Migration plan

### Phase 1 - Freeze the target model

- define the ClickHouse table inventory
- define engine, partition, and order keys per table
- choose naming convention for ids and domains
- document which normalized support dims survive

### Phase 2 - Stand up local ClickHouse

- add ClickHouse service to local Docker Compose
- add app configuration for ClickHouse connection
- verify bootstrap path can create databases and tables

### Phase 3 - Land raw archive first

- implement raw archive tables and write paths
- make each fetcher persist raw payloads before parsing
- verify replay from raw is possible for each source

### Phase 4 - Rebuild market storage

- replace Postgres market tables with ClickHouse equivalents
- port daily and minute ingestion first
- add reconciliation checks against small historical slices

### Phase 5 - Rebuild `alt_political`

- redesign normalized political schema into denormalized serving tables
- preserve raw payload lineage
- implement alias-resolution workflow for company mapping
- load one source at a time: trades, contracts, lobbying, donations, bills, hearings

### Phase 6 - Derived and experiments

- migrate signal outputs, snapshots, and experiment metadata
- ensure factor jobs read from ClickHouse only

### Phase 7 - FastAPI serving layer

- build endpoint contracts on top of serving tables
- add pagination, date filters, and freshness metadata
- benchmark common queries before adding more endpoints

### Phase 8 - VPS deployment and operations

- deploy single-node ClickHouse plus app containers
- schedule backups
- add ingestion health checks
- add storage growth monitoring

---

## Operational plan

### Local development

- Docker Compose with ClickHouse and app services
- `.env` contains ClickHouse connection settings
- bootstrap script creates database, users, and tables

### Production

- single VPS is acceptable for v1
- Dockerized ClickHouse with persistent volume
- daily compressed backups to off-box storage
- health checks for ingestion freshness and disk growth

### Backup and retention

- raw payloads retained long term
- curated tables retained long term
- backups must cover both raw and curated layers

---

## What changes in the codebase

- `src/factorlab/storage/db.py` should stop assuming Postgres-only SQLAlchemy setup
- current Alembic/Postgres migrations become legacy and should not drive the target design
- `src/factorlab/storage/schemas/*.py` should be replaced or isolated as legacy normalized specs
- new storage layer should describe ClickHouse DDL and ingestion contracts directly

---

## Open questions

- Should we keep SQLAlchemy at all for bootstrap work, or switch to explicit ClickHouse DDL files plus a thin client?
- For corrected event data, where do you want historical revisions preserved versus collapsed to latest?
- Do you want array/nested columns in ClickHouse for lobbying targets, witnesses, and sponsors, or duplicated child rows for simpler tooling?
