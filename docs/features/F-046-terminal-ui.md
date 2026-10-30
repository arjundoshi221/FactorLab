---
id: F-046
title: Redesign the hub as a trading terminal with Data, Portfolio and Ops sections
status: review
priority: P1
components: [web, api]
owner: unassigned
sprint: null
decisions: []
links: [F-036, F-037, F-040, F-042, F-044]
created: 2026-10-30
updated: 2026-10-30
---

# F-046 Redesign the hub as a trading terminal with Data, Portfolio and Ops sections

## Problem

The hub read as a simple operations dashboard: large hero headlines, explanatory paragraphs,
card grids and a navigation built around data plumbing (Data catalog, Schema, Pipelines,
Monitoring, Market data, Portfolios, an Operations menu). It is shown to investors as the
fund's platform, so it has to feel like a trading terminal: dense, chart-led, few labels,
every existing feature still reachable.

## Scope

- **Design system** ([tokens.css](../../components/web/src/theme/tokens.css),
  [terminal.css](../../components/web/src/theme/terminal.css)): near-black surfaces, hairline
  panels, amber accent, green/red direction colours, monospace tabular numbers, 12px base type.
  Chart slots are the validated dark categorical palette; up/down sit in the colour-blind warn
  band, so every signed value also carries ▲/▼ and a sign and up candles are hollow. Legacy page
  CSS uses the tokens (no raw colours) and a tighter spacing scale.
- **Shell** ([src/shell/](../../components/web/src/shell/)): header tabs **Home · Data ·
  Portfolio · Ops** with per-section sub-tabs from one route table (`nav.ts`), exchange clocks
  (NY/LDN/MUM regular sessions, holidays not applied), a system-status light from
  `/monitoring/summary`, a scrolling quote tape (tracked indexes and front-month futures), a
  status line, and a command bar (Ctrl+K or `/`; `g` then `h`/`d`/`p`/`o`/`m`/`t`) that jumps to
  any page or looks up a ticker through `/market-data/instruments`.
  - Data: Markets, Indexes, Futures, Matrix (Market Data views), India, US, Political,
    Catalog, Schema.
  - Portfolio: Book, Trades, Themes, Strategies, Trade names.
  - Ops: Overview (`/ops`, the former `/` dashboard), Monitoring, Pipelines, Coverage
    (`/market-data?view=collection`), Images, Roadmap.
- **Cockpit** (`/`, [Cockpit.tsx](../../components/web/src/pages/Cockpit.tsx)): independent
  panels that load, fail and refresh on their own: indexes with 3M sparklines, a sector heat
  grid, a futures board with term-structure sparklines and curve shape, the first active
  profile's book (KPIs, net-liquidation curve, holdings treemap), 1D movers, latest congressional
  trades and system health.
- **Chart kit** ([src/shared/charts/](../../components/web/src/shared/charts/)): Sparkline,
  AreaChart (crosshair, last-value tag), Candles (volume pane), Treemap (squarified), Gauge,
  SignedBars and the shared diverging heat scale. Every chart has an accessible label.
- **Page additions:**
  - Portfolio: Book adds gross exposure and unrealized KPIs (base-currency rows only), an equity
    curve with 1M/3M/1Y/MAX, and an allocation treemap.
  - Themes, Strategies and Trade names add PnL bars, one chart per currency.
  - Trades adds a tag-coverage meter and broker-realized PnL by fill day for the loaded page.
  - Markets: By sector adds a sector treemap; Futures adds a term-structure chart beside the
    price chart.
  - US adds a candlestick chart for the selected instrument, and India's chart moves to the
    shared candles.
  - Ops overview shows coverage gauges and size bars.
  - Long page descriptions move behind an ⓘ toggle in the header.
- **API:** `GET /hub/api/v1/portfolio/accounts/{broker}/{account}/equity?range=1m|3m|1y|max`
  returns the last broker-reported `NetLiquidation` per hour (1m) or day, account-wide (no
  segment) in the account's base currency, never summed across currencies. `/ops` is served as
  an SPA route by the API too.

## Non-goals

- No new runtime dependencies or third-party assets (CSP `default-src 'self'`).
- No light theme.
- No new data collection. The cockpit and charts only visualise data the API already returns,
  plus the equity history read from existing snapshots.

## Acceptance criteria

- [x] The header shows Data, Portfolio and Ops with the pages above. Every previous route still
  works, including the `/indexes` and `/futures` redirects.
- [x] `/` is the cockpit. The former dashboard, with its filters, sorting, polling and build line,
  is at `/ops`.
- [x] Every page keeps its filters, URL state, polling, editors, dialogs, pagination, CSV
  download and schema export.
- [x] The equity endpoint is profile-checked, base-currency-only and tested.
- [x] The layout works at phone width with no page-level horizontal scroll, and reduced motion
  stops the tape.

## Rollout

Release `api` (equity endpoint and `/ops` route) before or together with `web`. The web build
works without the endpoint: the equity panel shows "Equity history unavailable".

## Log

- 2026-10-30: created and implemented on `feat/terminal-ui`.
