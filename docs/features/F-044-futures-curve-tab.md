---
id: F-044
title: Show US futures curves and contracts on a Market Data Futures tab
status: review
priority: P2
components: [api, web, ingest-us]
owner: unassigned
sprint: null
decisions: []
links: [F-043, F-042]
created: 2026-10-22
updated: 2026-10-30
---

# F-044 Futures curves in the hub

## Problem

F-043 records US futures quotes every minute, but the hub shows none of it, and with
only front and next contracts per root there is no curve to look at.

## Scope

- Deepen the `configs/universes/us_futures.yaml` roster with a per-root `depth` (4 equity
  index, 12 energy, 5-6 metals, 3 rates, 4 FX; about 72 contracts).
- `GET /hub/api/v1/us/futures`: every root with its live contracts in expiry order and
  each contract's latest quote, change against the previous settlement, and a stale flag.
- A **Futures** view in Market Data (`/market-data?view=futures`, `/futures` redirects):
  group tabs, root chips, expiry and range selectors over a price-history chart of the
  selected contract, a contango/backwardation label, and a contract table.
- `GET /hub/api/v1/us/futures/{contract_id}/series?range=1d|5d|1m|3m|1y|max`: intraday
  last trades (1- or 15-minute buckets over the latest 1 or 5 sessions) or daily closes.

## Non-goals

- India stock futures (only the nearest contract is recorded, so there is no curve).
- A curve chart across expiries, overlays of past curves, continuous or back-adjusted series.

## Acceptance criteria

- [ ] After the `ingest-us` release every root shows its configured number of contracts.
- [ ] The selected contract's chart and the table match the latest `market.futures_quotes` rows for /ES and /CL.
- [ ] Quotes older than 15 minutes while Globex is open are flagged stale; a closed
      market shows the last session's curve without stale flags.
- [ ] Polling stays at about 2 Schwab requests a minute.

## Rollout

1. `ingest-us`: release the deeper roster (curve depth starts with the next session's roster refresh).
2. `api`: release `/us/futures`.
3. `web`: release the Futures view.

## Log

- 2026-10-22: created; roster deepened, API endpoint and Market Data Futures view built.
- 2026-10-30: the curve chart is replaced by a time series of one selected expiry
  (expiry and range buttons, URL-backed); new `/us/futures/{contract_id}/series` endpoint.
