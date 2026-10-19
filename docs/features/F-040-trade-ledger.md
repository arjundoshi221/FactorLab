---
id: F-040
title: Cross-account trade ledger with strategy, theme and trade-name tags
status: review
priority: P1
components: [api, web]
owner: unassigned
sprint: null
decisions: []
links: [F-036, F-038, F-039]
created: 2026-10-19
updated: 2026-10-19
---

# F-040 Cross-account trade ledger

## Problem

Tags are edited one order at a time, per account, 50 orders a page, and filters only see
the current page. There is no single place to check which trades carry which strategy,
theme and trade name, or which are still untagged.

## Scope

- `GET /hub/api/v1/portfolio/ledger` returns one row per order across a profile's
  accounts, with its theme, strategy and trade name, and tag status `tagged`, `untagged`
  or `not_taggable`.
- Filters run on the server before pagination: account, ticker, tag IDs, tag status and
  date. A second query returns the counts per tag status.
- Fills are de-duplicated by `exec_id` with `argMax(version)`, not `FINAL`.
- Web: `/portfolio/trades` with URL-backed filters, inline tag editing and bulk tagging.
  Bulk tagging sends the existing assignment PUT for each order, 2 at a time, and reports
  each order's result.
- An F-038 `tagging` info check: untagged orders per profile.

## Non-goals

- No batch write endpoint.
- No automatic tag inference.
- No FIFO PnL in the ledger; that stays on the Themes and Strategies pages.

## Acceptance criteria

- [x] Filters and counts cover the full history, not one page.
- [x] Only the owner profile's tags apply; currencies are never summed.
- [x] Bulk tagging reports partial failures and refetches.

## Rollout

`api` and `web` releases; no schema change.

## Log

- 2026-10-19: shaped as part of the monitoring/portfolio UX plan.
- 2026-10-19: implemented with focused API and web tests; awaiting release.
