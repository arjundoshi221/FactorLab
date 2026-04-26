# Architecture Overview

> Status: `[design]`

FactorLab is a multi-source equity research platform built to research-grade rigor with eventual production deployment as a daily signal pipeline. This document describes the conceptual layering. Concrete tech choices are in sibling docs.

---

## Three lifecycle stages

```
┌─────────────────┐     ┌────────────────┐     ┌───────────────────┐
│   RESEARCH      │ ──▶ │   BACKTEST     │ ──▶ │   DEPLOYMENT      │
│  factor design  │     │  signal eval   │     │  daily pipeline   │
│  hypothesis     │     │  PnL, capacity │     │  monitoring       │
└─────────────────┘     └────────────────┘     └───────────────────┘
        ▲                       ▲                       ▲
        └───── shared data + identifiers + code ────────┘
```

The platform must use **the same code paths** in research, backtest, and deployment. The only thing that changes is the data source (snapshot vs. live) and the time at which the signal is consumed. Anything else creates research-prod divergence — the #1 way personal platforms silently lie.

---

## Layered architecture

| Layer | Responsibility | Tech |
|-------|----------------|------|
| **Sources** | External APIs, scrapers, manual feeds | EODHD, Upstox, IBKR, Reddit/Twitter, Senate disclosures, arxiv |
| **Adapters** | Per-source auth, rate limiting, raw fetch | Python clients per provider |
| **Parsers** | Vendor-specific → canonical schema | Pure functions, vendor-isolated |
| **Storage** | Persistence, point-in-time integrity | PostgreSQL on Railway |
| **Models** | Canonical Pydantic types | `PriceBar`, `Filing`, `SocialPost`, `SenatorTrade`, `ResearchPaper` |
| **Compute** | Factors, transforms, signals | Pure functions on pandas/SQL |
| **Universe** | Time-indexed membership, survivorship | Postgres tables with effective-dating |
| **Backtest** | Vectorized → event-driven | In-process; results to Postgres |
| **Orchestration** | Daily jobs, retries, idempotency | TBD (Prefect/Airflow/cron) |
| **Interface** | Notebooks, dashboards | Jupyter, Streamlit |

---

## Cross-cutting principles

### 1. Point-in-time correctness is non-negotiable

Every fact stored has at least two timestamps: **`event_time`** (when it happened in the world) and **`as_of_time`** (when we knew it). Backtests filter on `as_of_time <= t` for every row. This applies to fundamentals (restatements!), index membership, senator filings (lag!), and even price data (corporate actions adjust history).

Schema rule: every fact table has both columns, indexed.

### 2. Stable internal security IDs

External tickers/symbols change. Companies merge, delist, get re-listed. Internal ID is a stable UUID per *security*, with vendor mappings in a separate table:

```
securities(security_id PK, name, country, asset_class, ...)
security_aliases(security_id FK, vendor, vendor_id, valid_from, valid_to)
```

Without this, the India expansion will hurt.

### 3. One vendor adapter per source, vendor logic stays at the edge

Adapters speak vendor JSON. Parsers translate to canonical models. **The rest of the codebase never imports a vendor SDK.** This is what makes Phase 5 (international) tractable.

### 4. Configuration over code, code over DSL

- Universes, factor params, schedule windows → YAML config
- Factor logic → Python functions
- Do **not** invent a YAML factor DSL. That trap eats 6 months and produces something less testable than Python.

### 5. Every source is a separate pipeline

Market data, social, political, and research feeds are different worlds — different cadence, schemas, trust levels, and downstream consumers. Don't force them into one ingestion framework. A common *interface* (`fetch -> parse -> persist`) yes; a common *implementation* no.

### 6. Reproducibility costs nothing if built in early

Every research/backtest run records: `(code_sha, config_hash, data_snapshot_id, params, result_uri)` in a Postgres `experiments` table. Without this, in 6 months you cannot answer "did factor X really work in May 2026?"

---

## What this platform is not

- Not a low-latency execution system. Daily-bar resolution is the floor; minute resolution where required (India intraday).
- Not a UI product. Streamlit dashboards for personal use; no public-facing app planned.
- Not a multi-user SaaS. One user, possibly multiple machines.

---

## Diagram: data flow per source

```
External API/scrape
        │
        ▼
   adapter.py        ← auth, rate-limit, retry, raw bytes
        │
        ▼
   parser.py         ← vendor JSON → canonical Pydantic models
        │
        ▼
  raw_<source>       ← Postgres table: full raw payload + as_of_time
        │
        ▼
  fact tables        ← Postgres: prices, fundamentals, posts, trades, papers
        │
        ▼
   compute/          ← factors, scores, signals (read fact tables only)
        │
        ▼
  signals / portfolios / experiments
```

Raw retention is non-negotiable: when a parser bug surfaces 6 months in, the only way back is the raw archive.
