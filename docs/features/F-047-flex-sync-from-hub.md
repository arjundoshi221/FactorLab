---
id: F-047
title: Run the IBKR Flex history sync on demand from the hub
status: review
priority: P2
components: [ingest-broker, api, web]
owner: unassigned
sprint: null
decisions: []
links: [F-036, F-040, F-045]
created: 2026-10-30
updated: 2026-10-30
---

# F-047 Flex history sync from the hub

## Problem

`broker.flex_activity` (Flex statement executions, executed orders and cash flows) runs
only by hand: an operator execs into `ibkr-snapshot` on the VPS and types the engine
command. The Flex token is IP-restricted to the VPS, so the hub cannot call IBKR, and
nothing in the hub could start an ingestion run.

## Scope

- ingest-broker: new backend-only service `ibkr-flex-runner` (`factorlab-ingest-broker
  flex-runner`, port 8091, no published port). `POST /runs` runs `engine run --dataset
  broker.flex_activity --market USA` as a subprocess; one run at a time (409) and a 300 s
  cooldown between starts (429). `GET /runs/latest` reports state, `run_id`, rows and errors.
- api: `POST /hub/api/v1/portfolio/flex/runs` (cross-site guarded) and
  `GET /hub/api/v1/portfolio/flex/runs/latest` proxy to the runner (`IBKR_FLEX_RUNNER_URL`);
  503 when the runner is unreachable. Pipeline card `broker.flex_activity:ibkr`.
- web: "Sync IBKR history" button on the Portfolio account header for live (non-simulated)
  IBKR accounts; polls every 5 s and refreshes the account when the run lands.

## Non-goals

- No schedule: the sync stays on demand.
- No custom date ranges; the saved Flex Query defines the period (at most 365 days).
- No job history beyond the latest job; `meta.ingestion_runs` is the record.

## Acceptance criteria

- [x] Runner, proxy and button covered by tests (`test_flex_runner.py`, `test_flex_sync.py`,
      `Portfolio.test.tsx`).
- [x] Local dry-run smoke test: the runner starts the engine and reports its outcome.
- [ ] In production, the button produces a new `meta.ingestion_runs` row for
      `broker.flex_activity:ibkr` and `broker.executions` / `broker.cash_flows` rows.

## Rollout

Release `ingest-broker` first (adds `ibkr-flex-runner` under the `ibkr` profile), then
`api`, then `web`. No migration and no new secret: the runner reuses `ibkr_client_runtime`
(`IBKR_FLEX_TOKEN`) and `IBKR_FLEX_QUERY_ID`.

## Log

- 2026-10-30: created; implemented, in review, pending the three component releases.
