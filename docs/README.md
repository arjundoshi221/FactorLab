# FactorLab - Development Documentation

Living documentation for the FactorLab platform. Each subfolder corresponds to a major architectural concern. Docs are intended to be read in roughly this order on first onboarding.

> **Guiding principle:** *Own what is structurally permanent. Continuously discover what will dominate next.*

## Structure

```text
docs/
|-- README.md
|-- API.md
|-- VPS.md
|-- architecture/            (design docs, ADRs)
|-- countries/               (per-country market notes)
|-- data-sources/            (one doc per vendor / feed)
|   |-- 01-us-equities-eodhd.md
|   |-- 02-india-markets-upstox.md
|   |-- 03-social-reddit-twitter.md
|   |-- 04-political-senator-trades.md
|   |-- 05-research-arxiv.md
|   |-- 06-ibkr.md
|   |-- 07-us-equities-schwab.md
|   |-- 08-india-fundamentals-filings.md
|   |-- 09-insider-supply-chain.md
|   |-- 10-india-fii-fpi-flows.md
|   |-- 11-macro-regime.md
|   |-- 11-us-schwab-ingestion.md
|   |-- 12-earnings-revisions-estimates.md
|   |-- 13-earnings-call-transcripts.md
|   |-- 14-short-interest-positioning.md
|   |-- 15-commodities-input-costs.md
|   |-- 16-index-reconstitution.md
|   |-- 17-regulatory-policy-events.md
|   |-- 18-fund-flows-buybacks-shareholding.md
|   |-- 19-alpha-research-stack.md
|   `-- 20-edgar-sec-filings.md           ← EDGAR (funds, insiders, 13F, XBRL, filings)
|-- developments/            (per-feature dev notes)
|-- operations/              (runbooks, scheduler entries)
|-- reform/                  (architecture cleanup docs)
|-- research/                (research-facing playbooks — signals, sources, workflows)
|   `-- etf-intelligence.md
|-- security/                (auth, secrets, PII, threat model)
`-- ops/                     (legacy — being migrated to operations/)
```

## Conventions

- Every data-source doc follows the same template: **Purpose -> Source -> Auth -> Rate Limits -> Schema -> Pipeline -> Storage -> Edge Cases -> Open Questions**.
- "Open Questions" is a real section, not a placeholder.
- Code references use `path:line` format.
- All times are stored in UTC internally.
- Credentials live in `.env` and are never committed.

## Status legend

| Tag | Meaning |
|-----|---------|
| `[design]` | Architecture decided, not yet built |
| `[scaffold]` | Skeleton code in place, no real ingestion |
| `[alpha]` | Working end to end on a small slice |
| `[beta]` | Full coverage, instrumented |
| `[prod]` | Deployed, monitored, signal-generating |

## Current direction

Single-store ClickHouse architecture with denormalized serving tables and REST-first access. See [API.md](API.md), [VPS.md](VPS.md), [architecture/02-database-clickhouse.md](architecture/02-database-clickhouse.md), [architecture/03-roadmap.md](architecture/03-roadmap.md), and [architecture/05-secrets-and-upstox-auth.md](architecture/05-secrets-and-upstox-auth.md).
