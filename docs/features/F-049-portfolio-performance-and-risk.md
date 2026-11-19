---
id: F-049
title: Portfolio performance tab and deeper risk analytics reconciled to IBKR
status: shipped
priority: P1
components: [api, web, ingest-broker]
owner: unassigned
sprint: null
decisions: []
links: [F-036, F-047, F-048]
created: 2026-11-19
updated: 2026-11-19
---

# F-049 Portfolio performance and risk analytics

## Problem

The live IBKR account (SGD base, margin-financed USD positions) had a risk board but no
answer to "how much am I actually making": no net profit after deposits, no time- or
money-weighted return, no breakdown of financing and trading costs, no benchmark and no
statistical risk. Leverage made the gap dangerous: a −8% day erased most of the year's
profit, and nothing showed drawdown or tail risk. Concentration, currency exposure and
FX/sector/cash-equivalent stress were also missing from the Risk tab, and Flex statements
only refreshed when someone pressed "Sync IBKR history".

## Scope

- Risk tab: gross-weight concentration (largest, top 3/5, effective holdings 1/Σw²),
  allocation by asset class, net exposure per currency (positions + signed cash, the
  margin debit counted once), % gross beside % NAV, available funds, stress scenarios for
  cash equivalents, USD vs base, the largest sector and a combined shock, with editable
  shock sizes; a one-year statistical risk card.
- API `GET /portfolio/accounts/{b}/{a}/performance?since=`: net profit, return on
  contributed capital, daily TWR (start-of-day flows), XIRR, monthly table, NAV vs capital
  series; attribution from IBKR's daily Flex `ChangeInNAV` (falling back to cash records),
  IBKR TWR for reconciliation, financing cost; volatility, historical VaR/ES 95%,
  drawdown, beta; S&P 500, Russell 3000 and equal-weight S&P benchmarks in the base
  currency plus a beta-matched benchmark.
- Web `/portfolio/performance` with period choice, charts, attribution, risk, monthly table.
- `ibkr-flex-runner` daily sync (`IBKR_FLEX_DAILY_AT`, default 06:30 New York).

## Non-goals

- Total-return equity benchmarks: Schwab has no S&P 500 total-return index and serves no
  distributions, and the production EODHD key is rejected (HTTP 401), so equity ETF
  benchmarks are price series. A renewed EODHD key would allow a dividend feed.
- Pre-2026 history backfill; server-side limits (`risk.budgets`).

## Acceptance criteria

- [x] YTD to 2026-11-12 reproduces the IBKR activity statement: TWR 20.67%, net profit
      S$4,807.63, financing S$305.68 and each attribution line (checked on production data).
- [x] Unconvertible amounts are named in warnings, never guessed; Flex `FixedString`
      currencies decode correctly.
- [x] Risk and Performance tabs render the new cards; web and API suites pass.
- [x] Deployed and the endpoint answers on production.

## Rollout

Push to `main` deploys `api` and `web` (branch UI trial). The daily Flex sync ships with an
ingest trial of `ingest-broker`. No migrations.

## Log

- 2026-11-19: created and implemented (Risk additions, performance service, IBKR
  reconciliation, analytics, Performance page, daily Flex schedule).
- 2026-11-19: shipped: api/web trial `f78524a` (performance endpoint verified on production:
  SGD, IBKR attribution, YTD TWR 8.33% = IBKR) and ingest trial
  `ingest-trial/ingest-broker/f78524a2e16b` (runner logs the daily 06:30 New York schedule).
- 2026-11-19: benchmarks SPY (S&P 500, `$SPX` until its history backfills), VT (global
  equity) and BOXX (cash, a total return because it accrues) added to `extra_symbols` in
  `deploy/us-universe.yaml` and the host's `/etc/factorlab/us-universe.yaml` (backup
  `us-universe.yaml.bak-20261119`); universe published them; IWV dropped from the page.
