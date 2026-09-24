# Architecture Overview

> Status: `[design]`

FactorLab is a multi-source equity research platform built to research-grade rigor with eventual production deployment as a daily signal pipeline. This document describes the conceptual layering. Concrete tech choices live in sibling docs.

---

## Three lifecycle stages

```text
RESEARCH -> BACKTEST -> DEPLOYMENT
    ^           ^            ^
    `----- shared data, identifiers, and code -----'
```

The platform should use the same core code paths in research, backtest, and deployment. The main things that change are the data source and the evaluation time.

---

## Layered architecture

| Layer | Responsibility | Tech |
|---|---|---|
| **Sources** | External APIs, scrapers, manual feeds | EODHD, Upstox, IBKR, Reddit/Twitter, Senate disclosures, arxiv |
| **Adapters** | Per-source auth, rate limiting, raw fetch | Python clients per provider |
| **Parsers** | Vendor-specific to canonical schema | Pure functions, vendor-isolated |
| **Storage** | Persistence, point-in-time integrity, raw retention | ClickHouse |
| **Models** | Canonical application types | Pydantic models and typed parser outputs |
| **Compute** | Factors, transforms, signals | Pure functions on SQL and dataframes |
| **Universe** | Time-indexed membership, survivorship | ClickHouse tables with versioned effective dating |
| **Backtest** | Vectorized to event-driven | In-process; results persisted to ClickHouse |
| **Orchestration** | Daily jobs, retries, idempotency | cron/systemd first, workflow engine later if needed |
| **Interface** | Notebooks, dashboards, APIs | Jupyter, FastAPI |

---

## Cross-cutting principles

### 1. Point-in-time correctness is non-negotiable

Every fact stored has at least two timestamps:

- `event_time` for when it happened in the world
- `as_of_time` for when we knew it

Backtests filter on `as_of_time <= t` for every row.

### 2. Stable internal security ids

External tickers change. The platform needs stable internal ids and separately maintained vendor mappings.

### 3. Vendor logic stays at the edge

Adapters speak vendor payloads. Parsers translate to canonical outputs. The rest of the codebase should not depend on vendor-specific SDKs or response shapes.

### 4. Denormalize where it helps

Serving tables should be optimized for research and API access, even if that means duplicating descriptive fields across curated facts.

### 5. Raw retention is permanent

Every source keeps a replayable raw archive in ClickHouse. Parser bugs and schema redesigns must be recoverable from retained payloads.

### 6. Reproducibility is part of the platform

Research and backtest runs should record code version, config hash, data snapshot identity, params, and outputs so old results can be explained later.

---

## What this platform is not

- Not a low-latency execution system
- Not a public SaaS product
- Not a multi-user platform

---

## Data flow per source

```text
External API/scrape
    |
    v
adapter.py
    |
    v
parser.py
    |
    v
raw_<source> tables in ClickHouse
    |
    v
curated fact and serving tables in ClickHouse
    |
    v
compute / APIs / research clients
```

Raw retention is non-negotiable: when a parser bug surfaces months later, the only safe way back is the raw archive.

### Provider contract (ClickHouse v2)

`src/factorlab/shared/ingest/provider.py` codifies this flow as typed parts every new provider builds on:

| Piece | Role |
|---|---|
| `RawCapture` | One response as received (`body` bytes, `request_key`, `transport`, `fetched_at`) |
| `storage.archive_raw(capture, source=, source_channel=)` | Immutable `raw.archive` row; returns the `raw_id` |
| pure `normalize(decoded capture)` | Vendor payload -> typed rows; reads only the archived bytes, so any row is replayable from raw |
| `Provenance` | `source`, `source_channel`, `raw_id`, `ingest_run_id`, `as_of_time`, `ingested_at`, stamped by storage at write time |
| `ingestion_run(...)` / `RunContext` | One `meta.ingestion_runs` row; `succeed_unit` / `fail_unit` isolate per-unit failures into `success` / `partial` / `failed` |
| `Provider` protocol + `run_provider` | `source`, `pipeline`, `market_code`, `collect(ctx)` |

Storage owns identity resolution (vendor id -> canonical ids via `ref.identifier_aliases`), enrichment from `ref.*` and `version`. The adapter owns only vendor facts. IBKR (`sources/ibkr/provider.py` with `storage/v2_broker.py`) is the first adopter. Schwab and Upstox follow the same pattern informally and can migrate onto the typed contract without changing their tables. The strict version of this contract (dataset sinks, provider adapters, config bindings) is specified in [07-ingestion-provider-abstraction.md](07-ingestion-provider-abstraction.md).
