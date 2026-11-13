---
id: F-048
title: Pivot portfolio holdings and tagged PnL by sector (and future attributes)
status: review
priority: P1
components: [api, web]
owner: unassigned
sprint: null
decisions: []
links: [F-036, F-037, F-042]
created: 2026-11-14
updated: 2026-11-14
---

# F-048 Pivot portfolio views by attribute

## Problem

The portfolio overview and the Themes, Strategies and Trade-names PnL pages show nothing
about sector, although market data already holds a current GICS sector for every
Russell 3000 listing (`ref.security_sector_history`, F-042). Users can't see how a theme
is spread across sectors, or how much of the book sits in one sector. More attributes
(country, asset class, market-cap bucket) will be wanted later, and they should not need
new UI code.

## Scope

- **API, `portfolio_attributes.py`**: an attribute registry.
  - Each attribute classifies a resolved instrument. Resolution order: IBKR conid →
    `ibkr_conid` alias → listing or contract; a contract → its underlying listing.
  - `GET /hub/api/v1/portfolio/dimensions` lists the attributes with their buckets in
    display order.
  - `dims=sector` on `/accounts/{broker}/{account}/positions` and on `/pnl/{kind}` labels
    each row with `attributes.sector`. For PnL this covers both `details` and `excluded`.
  - Sector is the current GICS sector. Sector SPDR and Invesco equal-weight ETFs take
    their sector from `catalog_us.yaml`. Other ETFs go to "ETF / multi-sector". Anything
    else goes to "Unclassified".
  - A failed lookup is listed in `unavailable_dimensions`. It is never reported as
    Unclassified.
- **Web, generic layer**:
  - `pivot.ts`: `pivotRows`, exact `sumDecimals`, `useDimensions`, and URL-backed
    `pivot=` / `rows=attr` parameters.
  - `PivotControl`: the "Pivot by" select, plus the row-order flip.
- **Tag pages**:
  - Group → Sector shows sector subtotals inside each expanded group.
  - Sector → Group shows sector rows, each expanding into per-group subtotals.
  - Subtotals follow the API's `summarize` rules (`pnlPivot.ts`): unvalued holdings are
    excluded with their fees counted, unknown costs make fees unavailable, and IBKR
    figures are net of commissions. Currencies, modes and simulations never merge.
- **Overview**:
  - The Allocation treemap shows sector tiles.
  - An "Exposure by sector" table shows positions, gross, share of gross, net and
    unrealized in the base currency. Each row expands to its positions.
  - A position that can't be converted makes its bucket unavailable rather than partial
    (`exposurePivot.ts`).

## Non-goals

- Point-in-time sectors (history starts at the first IWV snapshot).
- Industry or sub-industry levels, and non-US sectors (no source yet).
- A sector-concentration table on Risk and a sector filter on the trade ledger. These can
  reuse the same pivot later.

## Acceptance criteria

- [x] `dims=sector` labels positions and PnL holdings; without `dims` the responses are unchanged apart from an empty `unavailable_dimensions` on snapshots.
- [x] A stock gets its sector; an option or future gets its underlying's; XLK gets IT; IWV gets ETF / multi-sector; an unresolved conid gets Unclassified.
- [x] Sector subtotals inside a group reconcile with the group's totals.
- [x] Sector → Group never merges currencies, account modes or simulations.
- [x] The overview exposure is in the base currency, and an unconvertible position makes its bucket unavailable.
- [x] The pivot choice survives a reload (URL), and a classification failure is shown as a notice with the report left unpivoted.
- [ ] Checked against production data on the VPS.

## Rollout

`api` and `web` ship together. A push to `main` deploys `api-candidate` and `web`
through the branch UI trial. There is no migration: it reads the existing `ref.*` tables.

## Log

- 2026-11-14: created and implemented (API registry and routes, web pivot on overview and tag pages); in review.
