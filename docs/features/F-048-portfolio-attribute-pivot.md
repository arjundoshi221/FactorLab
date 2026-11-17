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
updated: 2026-11-16
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
  - `pivot.ts`: `pivotRows`, the N-level `pivotTree`, exact `sumDecimals`,
    `useDimensions`, and the URL-backed grouping `group=theme,sector` (`useGroupBy`; the
    older `pivot=` / `rows=attr` links still open the same view).
  - `GroupByChips`: the grouping levels as ordered chips. Drag them or use ‹ › to reorder,
    × to remove (at least one level remains), and "+ Add level" to nest another. The
    levels are the three order tags (Theme, Strategy, Trade name) plus every API
    dimension.
  - `PivotControl` (single "Pivot by" select) remains for the overview.
- **Tag pages**: one treegrid with a section per currency, account mode and
  simulation.
  - Each section has a header row with its totals. The same columns (realized,
    unrealized, fees, total with an inline diverging bar, IBKR) appear at every depth,
    down to the holdings.
  - Rows can be expanded independently, with Expand all and Collapse all. The first
    level is open when there are several levels.
  - Clicking a column header sorts the rows by that column (Total, descending, by
    default). Unclassified and Untagged sort last.
  - When the first level is the page's own tag, its rows carry the API's group totals.
    Deeper levels, and any other order, re-aggregate holdings with the API's
    `summarize` rules (`pnlPivot.ts`, `pnlSections`): unvalued holdings are excluded with
    their fees counted, unknown costs make fees unavailable, and IBKR figures are net of
    commissions.
  - Long excluded-instrument lists are shortened to a count, with the full list in the
    tooltip.
  - Reconciliation and Account fees are folded under the table, with their counts in
    the summary.
- **Risk**: an "Exposure by group" tree (default Theme › Sector) shows gross, % of NAV,
  net, β-adjusted exposure and unrealized in the base currency.
  - Positions are split across tags by the open quantity of each tagged holding from
    `/pnl/themes` (`riskExposure.ts`). The remainder is Untagged.
  - An unconvertible position makes its totals unavailable.
  - The page is reordered as follows: key metrics; exposure beside the snapshot and
    capacity; positions beside composition; leverage. "Limits & operating envelope" and
    "Stress tests & triggers" are folds that open when a limit is breached or the
    verdict is red.
  - "Positions & concentration" has its own "Pivot by" (URL `by=sector`, separate from the
    exposure tree's `group=`): positions sit under a subtotal row per sector (value, % NAV,
    value-weighted beta, unrealized, realized YTD; a position without an FX rate makes its
    sector's total unavailable), and Composition becomes a donut by sector. Single-name
    limits still apply per position.
- **Overview**:
  - "Pivot by" sits in the account toolbar and applies to the whole account page (URL
    `pivot=sector`).
  - The Allocation treemap shows sector tiles.
  - An "Exposure by sector" table shows positions, gross, share of gross, net and
    unrealized in the base currency. Each row expands to its positions.
  - A position that can't be converted makes its bucket unavailable rather than partial
    (`exposurePivot.ts`).
  - Open positions are grouped under a header row per sector, and the account's open and
    closed trades show each trade's sector (the "View all" link carries the pivot).
- **Trades** (both the Orders ledger and Open & closed): a "Pivot by" select in the filter
  bar (URL `pivot=sector`). The API labels every filtered row (`dims=sector` on `/ledger`
  and `/trades`) and returns `pivots`: per sector over full matching history (not just the
  page) and ignoring the sector filter itself. For trades: open and closed counts,
  unrealized of open trades and closed results (net, else realized) per currency, mode and
  simulation, and the win rate. For orders: counts by tag status and tag coverage. Choosing a
  sector filters the list on the server (URL `bucket=<id>`, `none` for Unclassified; API
  `bucket=sector:<id>`), and rows show their sector under the ticker. If the lookup fails,
  the notice says so and the bucket filter is not applied.

## Non-goals

- Point-in-time sectors (history starts at the first IWV snapshot).
- Industry or sub-industry levels, and non-US sectors (no source yet).

## Acceptance criteria

- [x] `dims=sector` labels positions and PnL holdings; without `dims` the responses are unchanged apart from an empty `unavailable_dimensions` on snapshots.
- [x] A stock gets its sector; an option or future gets its underlying's; XLK gets IT; IWV gets ETF / multi-sector; an unresolved conid gets Unclassified.
- [x] Sector subtotals inside a group reconcile with the group's totals.
- [x] Sector → Group never merges currencies, account modes or simulations.
- [x] The overview exposure is in the base currency, and an unconvertible position makes its bucket unavailable.
- [x] The pivot choice survives a reload (URL), and a classification failure is shown as a notice with the report left unpivoted.
- [x] Any order and depth of tags and attributes; old `pivot=` links still work; currencies are separate sections.
- [x] Risk exposure by theme/sector splits each position by tagged quantity, and its totals reconcile with gross exposure.
- [x] Trades (orders and open/closed) pivot and filter by sector across full history; sector results never add currencies.
- [x] Overview open positions and account trades follow the page pivot; Risk positions and composition pivot by sector.
- [ ] Checked against production data on the VPS.

## Rollout

`api` and `web` ship together. A push to `main` deploys `api-candidate` and `web`
through the branch UI trial. There is no migration: it reads the existing `ref.*` tables.

## Log

- 2026-11-14: created and implemented (API registry and routes, web pivot on overview and tag pages); in review.
- 2026-11-16: tag pages redesigned as a currency-sectioned treegrid with Group-by chips (N levels, URL `group=`); Risk gains Exposure by group and a decluttered layout.
- 2026-11-17: sector pivot extended to Trades (`dims=`/`bucket=` on `/ledger` and `/trades`, per-sector summaries), Overview open positions and account trades, and Risk positions and composition (`by=`).
