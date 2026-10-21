---
id: F-043
title: Record US futures quotes and daily contract bars from Schwab REST
status: in-progress
priority: P2
components: [ingest-us, schema-migrator, storage]
owner: unassigned
sprint: null
decisions: [ADR-0002]
links: [F-021, F-041]
created: 2026-10-21
updated: 2026-10-21
---

# F-043 US futures from Schwab REST quotes

## Problem

FactorLab records no US futures. Schwab `/pricehistory` does not serve futures, and the
streaming recorder that would deliver 1-minute futures bars (F-021) is unbuilt and needs a
`/trader/v1/userPreference` exception to the no-trader-endpoints rule.

## Scope

- Poll `/marketdata/v1/quotes` about once a minute through the Globex week for a roster of
  14 roots (equity index, energy, metals, rates, FX), front and next contract each.
- Root listings in `ref.listings` and dated contracts in `ref.contracts`, alias kind
  `schwab_symbol`; new product types `commodity_future`, `rate_future`, `fx_future`.
- Intraday snapshots in a new `market.futures_quotes` table (contract keyed, no TTL),
  skipping rows whose vendor quote time has not moved.
- One daily bar per contract and trade date in `market.futures_contract_bars`, close
  rewritten to the official settlement once the next session's quote carries it.
- A `futures-us` service in the `ingest-us` component.

## Non-goals

- 1-minute or tick futures bars (F-021 streaming).
- Futures history before the recorder starts; Schwab has no REST futures history.
- Continuous or back-adjusted series and roll rules.
- Any `/trader/v1` call.

## Acceptance criteria

- [ ] The roster resolves front and next contracts for every root Schwab quotes; an
      unquoted root is logged and skipped.
- [ ] `market.futures_quotes` fills through a full Globex night with no duplicate quote times.
- [ ] Each contract has one daily bar per trade date, and its close equals the exchange
      settlement after the next session opens.
- [ ] Polling costs about 1 request a minute against the Schwab account pool.

## Rollout

1. `schema-migrator`: apply `wave_15_schema_futures_quotes.sql` (reviewed, forward only).
2. `ingest-us`: release with the new `futures-us` service and bindings.

## Log

- 2026-10-21: created and started; REST quote polling chosen over streaming.
- 2026-10-21: built (wave 15, sink, Schwab futures sources, `futures-us` service). Futures
  sources stay unregistered until a recorded live `/quotes` capture confirms field names
  and becomes their fixture; `/6J` left out of the roster (7-decimal tick).
- 2026-10-21: waves 14 and 15 applied in production; `futures-us` deployed by the new
  automated ingestion trial (`ingest-trial/futures-us/62ebd99e91cf`). Live responses
  showed a root is answered by its active contract keyed by the contract symbol (no
  `futureActiveSymbol`), so no root resolved and every roster refresh failed; `/VX` and
  `/DX` return `invalidSymbols` and left the roster (14 roots). Fixed forward.
- 2026-10-21: the session-close query aliased `max(observed_at)` as `observed_at`
  (ClickHouse ILLEGAL_AGGREGATION); fixed in `c44df22`. Recording live: 28 contracts
  quoted every minute into `market.futures_quotes`.
