# FactorLab — Documentation

Living documentation. Each subfolder is a concern; this index is the map.

> **Guiding principle:** *Own what is structurally permanent. Continuously discover what will dominate next.*

## Layout

```
docs/
├── README.md                          <- you are here
├── CONTRIBUTING.md                    <- how to land changes
│
├── architecture/                      <- HOW the system is built
│   ├── 01-overview.md                 <- system layers, lifecycle, principles
│   ├── 03-roadmap.md                  <- phased build plan
│   ├── database.md                    <- canonical schema + storage + hosting (single source of truth)
│   └── ingestion-inventory.md         <- index of every script/module that fetches data
│
├── countries/                         <- per-jurisdiction overview docs
│   ├── india-equities.md
│   └── us-equities.md
│
├── data-sources/                      <- one folder per domain; one file per vendor
│   ├── us/        eodhd, schwab, ibkr
│   ├── india/     upstox, fundamentals-filings, fii-fpi-flows
│   ├── political/ senator-trades · pipeline · historical-loads · senate-efd-host
│   ├── alt/       social-reddit-twitter, research-arxiv, insider-supply-chain, short-interest-positioning
│   └── cross/     macro-regime, commodities-input-costs, earnings-revisions-estimates,
│                  earnings-call-transcripts, index-reconstitution, regulatory-policy-events,
│                  fund-flows-buybacks-shareholding
│
├── developments/                      <- forward-looking architecture decisions & dev plans
│   └── (numbered: 001, 002, ...)
│
├── operations/                        <- runbooks: how to operate the live system
│   ├── orchestrators.md               <- bird's-eye script registry + live-daemon conventions
│   ├── windows-task-scheduler.md      <- authoritative Task Scheduler job list
│   ├── env-reference.md               <- canonical env var list
│   ├── nas-storage.md                 <- one-way backup mirror design
│   ├── notifier-daemon.md             <- Outlook-COM service spec
│   └── backfill-cli.md                <- one-shot ingest CLI
│
└── security/                          <- security posture
    ├── SECURITY.md                    <- policy: how to report, what's in scope
    └── audit-2026-05-08.md            <- multi-agent audit findings
```

## Where things live (decision tree)

- **"How is table X laid out?"** → [`architecture/database.md`](architecture/database.md)
- **"Where does this script run, and when?"** → [`operations/orchestrators.md`](operations/orchestrators.md) → [`operations/windows-task-scheduler.md`](operations/windows-task-scheduler.md)
- **"What does vendor V give us?"** → `data-sources/<country-or-domain>/<vendor>.md`
- **"What env var does X read?"** → [`operations/env-reference.md`](operations/env-reference.md)
- **"What's the plan for feature Y?"** → `developments/`
- **"Is this change a security concern?"** → [`security/SECURITY.md`](security/SECURITY.md)

## Conventions

- Every data-source doc follows the same template: **Purpose → Source → Auth → Rate Limits → Schema → Pipeline → Storage → Edge Cases → Open Questions**.
- "Open Questions" is a real section, not a placeholder — capture decisions still owed.
- Code references use `path:line` format.
- All times in UTC internally; presentation in local exchange time only.
- All credentials live in `.env` (per `operations/env-reference.md`); never commit secrets.
- When schemas change in code, update `architecture/database.md` in the same commit. The doc is the contract.

## Status legend

| Tag | Meaning |
|-----|---------|
| `[design]` | Architecture decided, not yet built |
| `[scaffold]` | Skeleton code in place, no real ingestion |
| `[alpha]` | Working end-to-end on a small slice |
| `[beta]` | Full coverage, instrumented |
| `[prod]` / `[live]` | Deployed, monitored, signal-generating |

## Phase 1 (current)

US equities via Schwab + EODHD + (future) IBKR. India via Upstox. US political-alt-data live across 8 sources. See [`architecture/03-roadmap.md`](architecture/03-roadmap.md) for what comes next.
