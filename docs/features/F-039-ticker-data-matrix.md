---
id: F-039
title: Ticker × dataset matrix linking each ticker's data, holdings and trades
status: review
priority: P1
components: [api, web]
owner: unassigned
sprint: null
decisions: []
links: [F-037, F-038, F-040]
created: 2026-10-19
updated: 2026-10-19
---

# F-039 Ticker × dataset matrix

## Problem

The instrument explorer (F-037) answers what is stored for one ticker. Nothing shows,
across tickers, which ones have fetched their data and which FactorLab datasets each
ticker has. There is also no link from a ticker to its holdings, trades and tags.

## Scope

- `GET /hub/api/v1/market-data/matrix` returns one row per listing, with cells for:
  - bars per configured (country, source, resolution);
  - futures (underlying);
  - fundamentals (issuer-level);
  - political trades;
  - positions;
  - trades;
  - tags.
- Cell states are `ok`, `stale`, `missing`, `none`, `not_configured`, `needs_tags` and
  `unavailable`.
- Queries are set-based, one per column group over the page's IDs, so the count does not
  depend on page size.
- Attention filters are applied before pagination.
- Web:
  - a "Data matrix" view in `/market-data`;
  - clicking a ticker opens the F-037 detail with an "at a glance" strip;
  - Trades and Tags cells link to the trade ledger (F-040).
- An F-038 `tickers` check: configured series with no bar for the latest completed session.

## Non-goals

- No ingestion repair or new identity scheme.
- No allocation of account totals to instruments.

## Acceptance criteria

- [x] Constant query count for page sizes 1, 50 and 100; failed columns are `unavailable`.
- [x] Contract records never enter listing cells as direct matches.
- [x] Every cell links to its evidence.

## Rollout

`api` and `web` releases; no schema change.

## Log

- 2026-10-19: shaped as part of the monitoring/portfolio UX plan.
- 2026-10-19: implemented with focused API and web tests; awaiting release.
- 2026-10-19: deployed via the API/web branch trial `0.0.0-trial.8bea6df4a265` (commit `8bea6df`); verification passed. See [VPS state](../VPS.md).
