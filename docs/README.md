# FactorLab — Documentation

Living documentation. Each subfolder is a concern; this index is the map. Code-level
context for each workspace member lives next to the code in its `CONTEXT.md`; the
repository rules are in [AGENTS.md](../AGENTS.md).

> **Guiding principle:** *Own what is structurally permanent. Continuously discover what will dominate next.*

## Layout

```text
docs/
├── README.md                          <- you are here
├── API.md                             <- ClickHouse REST API
├── VPS.md                             <- VPS deployment and operations
├── CONTRIBUTING.md                    <- setup (uv), checks, hooks, how to land changes
│
├── architecture/                      <- HOW the system is built
│   ├── 01-overview.md                 <- system layers, lifecycle, principles
│   ├── 02-database-clickhouse.md      <- why ClickHouse single-store
│   ├── 03-roadmap.md                  <- historical phase plan (superseded by features/)
│   ├── 05-secrets-and-upstox-auth.md  <- Cloudflare secrets authority + broker OAuth
│   ├── 06-schema-rehau.md             <- ClickHouse v2 schema (authoritative DDL source)
│   ├── 07-ingestion-provider-abstraction.md <- provider adapters -> engine -> DB-service sinks
│   ├── 08-repository-layout.md        <- uv workspace, dependency rules, recipes, tooling
│   ├── database.md                    <- canonical schema + storage + hosting
│   └── ingestion-inventory.md         <- index of every script/module that fetches data
│
├── decisions/                         <- ADRs (NNNN-<slug>.md, frontmatter status)
├── features/                          <- planned work (F-NNN-<slug>.md) + generated index
├── sprints/                           <- two-week sprints (YYYY-Sxx.md) + generated lists
│
├── countries/                         <- per-jurisdiction overview docs
├── data-sources/                      <- one folder per domain; one file per vendor
│   ├── us/        eodhd, schwab, ibkr
│   ├── india/     upstox, fundamentals-filings, fii-fpi-flows
│   ├── political/ senator-trades · pipeline · historical-loads · senate-efd-host
│   ├── alt/       social-reddit-twitter, research-arxiv, insider-supply-chain, short-interest-positioning
│   └── cross/     macro-regime, commodities-input-costs, earnings-revisions-estimates, ...
├── research/                          <- research-facing signals, sources, and workflows
│
├── operations/                        <- runbooks: how to operate the live system
│   ├── releases.md                    <- per-unit releases, rollback classes, checks
│   ├── rollout.md                     <- monolith -> per-component rollout checklist (R0-R5)
│   ├── log-access.md                  <- restricted log reader, read_logs.py
│   ├── clickhouse-v2-data-completion.md
│   ├── env-reference.md               <- env var reference (.env.example is generated)
│   └── ...                            <- older runbooks (orchestrators, Task Scheduler, NAS)
│
└── security/                          <- security posture
```

## Where things live (decision tree)

- **"How is the repo organised / how do I add a provider, component or dataset?"** → [`architecture/08-repository-layout.md`](architecture/08-repository-layout.md)
- **"What does member X do and how do I change it safely?"** → that member's `CONTEXT.md`
- **"How is table X laid out?"** → [`architecture/06-schema-rehau.md`](architecture/06-schema-rehau.md)
- **"How do I add / switch / duplicate a data provider?"** → [`architecture/07-ingestion-provider-abstraction.md`](architecture/07-ingestion-provider-abstraction.md) §12
- **"How do I release / roll back?"** → [`operations/releases.md`](operations/releases.md)
- **"What does vendor V give us?"** → `data-sources/<country-or-domain>/<vendor>.md`
- **"What env var does X read?"** → [`../.env.example`](../.env.example) (generated) and the member's `CONTEXT.md`
- **"What's planned, and when?"** → [`features/`](features/README.md), [`sprints/`](sprints/README.md)
- **"Why is it built this way?"** → [`decisions/`](decisions/README.md)
- **"Is this change a security concern?"** → [`security/SECURITY.md`](security/SECURITY.md)

## Conventions

- Every data-source doc follows the same template: **Purpose -> Source -> Auth -> Rate Limits -> Schema -> Pipeline -> Storage -> Edge Cases -> Open Questions**.
- "Open Questions" is a real section, not a placeholder.
- All times in UTC internally; presentation in local exchange time only.
- Credentials live in the Cloudflare secret stores (runtime secret volumes in production, a local `.env` for development); never commit secrets.
- When schemas change in code, update `architecture/06-schema-rehau.md` in the same commit.
- `uv run python tools/check_docs.py` checks links, frontmatter, generated indexes and the `CONTEXT.md` files; CI runs it.

## Status legend

| Tag | Meaning |
|-----|---------|
| `[design]` | Architecture decided, not yet built |
| `[scaffold]` | Skeleton code in place, no real ingestion |
| `[alpha]` | Working end to end on a small slice |
| `[beta]` | Full coverage, instrumented |
| `[prod]` / `[live]` | Deployed, monitored, signal-generating |

## Current direction

The store is ClickHouse v2 on the VPS (application cutover 2026-09-24). Ingestion is moving
from the legacy daemons to the provider-agnostic engine one component at a time (every
binding is still `shadow`), and production is moving from one monolith image to
per-component releases ([rollout](operations/rollout.md)). US equities use Schwab, EODHD and
IBKR; India uses Upstox; political data comes from the House Clerk and legislator
providers, with further sources to be re-ported. See [the features](features/README.md)
for what is planned.
