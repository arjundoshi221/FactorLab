---
id: F-036
title: Profile-owned order tags and portfolio PnL
status: shipped
priority: P1
components: [schema, storage, api, web]
owner: unassigned
sprint: null
decisions: []
links: []
created: 2026-10-15
updated: 2026-11-13
---

# F-036 Profile-owned order tags and portfolio PnL

## Problem

Imported broker fills show account activity but cannot yet answer how a theme,
strategy or named trade is performing across a profile's broker accounts.

## Scope

- Independent, profile-owned themes, strategies and trade names, with create,
  edit, archive and restore. Referenced entries retain stable IDs.
- Complete three-tag assignments per broker/account/durable perm_id. All current
  and future partial fills inherit the latest assignment. Clearing and retagging
  preserve an append-only audit and restate calculations.
- FIFO within account/instrument/currency/exact tag triple. Longs, shorts, partial
  closes and reversals are supported for stocks and ETFs. Other products remain
  visible and taggable with unavailable PnL.
- Separate theme, strategy and trade-name screens with holdings/order drill-down.
  Currency, account mode and simulation status are separate aggregation dimensions.
- Gross realized and unrealized PnL, commissions and total = realized + unrealized
  - commissions. Cross-currency commissions are expenses in their own currency.
- Results cover available recorded fills. Snapshot-cutoff quantity reconciliation
  detects missing history. Missing basis/prices/costs are unavailable, never zero.
  Stale valuations retain timestamps. A quantity match does not prove inception
  history is complete; reported broker realized PnL is not the tagged FIFO basis.
- Period view (added 2026-11-13): `GET /pnl/{kind}?since=YYYY-MM-DD` (the UI's
  "YTD" toggle sends 1 January). The date is a New York trading day, matching IBKR
  statements. FIFO still runs over full history, so lots opened earlier keep their
  cost; realized PnL counts only closing fills in the window and commissions only
  fills in the window. Unrealized remains the current open-lot mark, so the period
  `total` is net realized (realized - commissions). Groups with no fills in the
  window and no open lots are omitted.
- Account fees: the response's `account_fees` lists Flex cash transactions of type
  `fee` (other/broker fees, e.g. market data) and `commission` (commission
  adjustments) per account and currency, as positive costs, for the same window.
  They are not tied to a fill, so they are shown beside the tagged table and are
  not part of any theme/strategy/trade-name total. Gateway-only accounts have none.
  `account_fees` also carries `fx_conversions`: commissions on currency conversions.
- Currency conversions (`product_type` `forex`) are cash movements, not positions:
  they never enter tagged PnL (their commissions are reported with account fees).
- Partial group totals (2026-11-13): an instrument that cannot be valued (e.g. shares
  bought before the recorded history) is left out of its group's realized/unrealized
  and listed in the group's `excluded`; its fees still count, so the group `total` is
  the valued instruments' PnL minus all fees. A fill with an unknown cost (including a
  Gateway fill recorded with commission 0 and no currency before IBKR's commission
  report) still makes the group's fees and total unavailable. Instrument rows keep
  their own unavailable values.
- Gateway fills recorded before their commission report get the commission (and, if
  missing, realized PnL) from the next Flex statement, as a new version of the same
  row; perm_id, order id and client id, which carry the order's tags, are kept.

## Non-goals

- Historical charts, FX conversion, dividends, interest, financing, derivative PnL,
  manual lot allocation, automatic tag inference, and private-user authorization.
- Altering imported broker facts or automatically assigning legacy unowned catalogs.

## Acceptance criteria

- [x] Profiles cannot select entries from another profile; catalogs are independent.
- [x] Existing/future partial fills inherit tags without rewriting ingestion data.
- [x] Each of the three grouped views reconciles to the same underlying ledger.
- [x] Retagging, clearing, duplicate fills, shorting and reversals are covered by tests.
- [x] Missing history, prices and commissions produce explicit unavailable totals.
- [x] Forward DDL and read/write queries are exercised on disposable ClickHouse.

## Rollout

Apply pending Wave 12 profile schema, then forward Wave 13 portfolio tagging before
releasing api and web. These are separate operator operations. API database
credentials need SELECT on existing portfolio sources and INSERT on the three
book registries and book.order_assignment_events. No new public origin is required.

The API reads at most 20,000 fills per report and 20 accounts for an aggregate;
requests exceeding those bounds fail rather than return truncated PnL. Materialized
rollups are deferred. Everyone admitted to the existing shared workspace can edit
the profile catalogs; a profile selector is not an authorization boundary.

## Portfolio UI

The account screen puts net liquidation, cash and snapshot counts above the order
workspace and holdings. Headline balances use unsegmented metrics in the stated
base currency; missing values remain unavailable. Working orders, executions and
all balance/position fields remain accessible under Account activity.

Each order opens its own tag editor; current and future fills inherit its saved
triplet. Refreshes preserve unfinished forms and last successful results. Search
and tag-status filters apply to the current 50-order page, with pagination when
needed. The three PnL screens compare groups in tables, expand into holdings and
contributing account links, and expose calculation notes separately. Currencies,
account modes and simulations remain separate. Display rounding preserves the
underlying decimal string in the value title. Catalog creation/editing is
on-demand; archived entries are available through Show archived and Restore.
Profile/account selections carry through navigation URLs.

## Log

- 2026-10-19: the local portfolio UI cleanup shipped with the F-038 to F-040 branch
  trial `0.0.0-trial.8bea6df4a265`; see [VPS state](../VPS.md).
- 2026-10-15: Cleaned up the portfolio UI locally: compact navigation, account
  summary cards, orders beside holdings, expandable tag forms and activity,
  PnL comparison tables and a catalog library with on-demand creation/editing.
  Added page-level order search/status filters, consistent decimal formatting,
  profile/account navigation context and draft-preserving refreshes. This UI
  update has not been deployed yet.
- 2026-10-15: Implemented schema, API, ledger, UI and local verification; not deployed.
- 2026-10-15: Applied Waves 12/13 and deployed the API/web branch trial from
  `743777d`. CI and live synthetic assignment, grouped PnL, audit and clear checks
  passed. Partial AAPL order and demo catalogs are ready for manual testing;
  see [VPS state](../VPS.md). Authenticated browser verification remains pending.
