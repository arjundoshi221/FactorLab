# FactorLab — Development Documentation

Living documentation for the FactorLab platform. Each subfolder corresponds to a major architectural concern. Docs are intended to be read in roughly this order on first onboarding.

> **Guiding principle:** *Own what is structurally permanent. Continuously discover what will dominate next.*

## Structure

```
docs/
├── README.md                       <- you are here
├── architecture/
│   ├── 01-overview.md              <- system layers, lifecycle, principles
│   ├── 02-database-postgres.md     <- PostgreSQL on Railway, schema strategy
│   └── 03-roadmap.md               <- phased build plan
├── data-sources/
│   ├── 01-us-equities-eodhd.md
│   ├── 02-india-markets-upstox.md
│   ├── 03-social-reddit-twitter.md
│   ├── 04-political-senator-trades.md
│   ├── 05-research-arxiv.md
│   └── 06-ibkr.md                  <- broker: data + paper trading + execution
└── ops/                            <- (future) deployment, monitoring, runbooks
```

## Conventions

- Every data-source doc follows the same template: **Purpose → Source → Auth → Rate Limits → Schema → Pipeline → Storage → Edge Cases → Open Questions**.
- "Open Questions" is a real section, not a placeholder — capture decisions still owed.
- Code references use `path:line` format.
- All times in UTC internally; presentation in local exchange time only.
- All credentials live in `.env`; never commit secrets.

## Status legend

| Tag | Meaning |
|-----|---------|
| `[design]` | Architecture decided, not yet built |
| `[scaffold]` | Skeleton code in place, no real ingestion |
| `[alpha]` | Working end-to-end on a small slice |
| `[beta]` | Full coverage, instrumented |
| `[prod]` | Deployed, monitored, signal-generating |

## Phase 1 (current)

US equities ingestion via EODHD with PostgreSQL persistence. See [architecture/03-roadmap.md](architecture/03-roadmap.md) for what comes next.
