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
