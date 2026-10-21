---
id: F-042
title: Track official US indexes and show equal- and cap-weighted GICS sector indexes over the Russell 3000
status: review
priority: P1
components: [ingest-us, api, web, schema-migrator, storage, platform]
owner: unassigned
sprint: null
decisions: [ADR-0017]
links: [F-041]
created: 2026-10-21
updated: 2026-10-21
---

# F-042 US indexes and sector performance

## Problem

Market data covers single US stocks only. Official index values (`$SPX`, `$COMPX`, ...)
were dropped by the Schwab normalizer. There was no sector classification and no market
cap. As a result, the hub could show neither index levels nor how sectors are performing.

## Scope

- **Official indexes and benchmark ETFs.** Collect Schwab daily bars for `$SPX $COMPX $NDX
  $DJI $RUT $VIX`, the 11 sector SPDRs, `RSP` with the 11 Invesco equal-weight sector ETFs,
  and `IWV`.
- **New `ishares` provider.** It reads the IWV holdings file as the Russell 3000 membership
  proxy (`r3k_iwv`) and as the source of GICS sectors (`ref.security_sectors` into
  `ref.security_sector_history`).
- **Shares outstanding.** A daily Schwab snapshot into `fundamentals.shares_outstanding`.
- **Index computation.** `factorlab.indexes` and the `indexes-us` service compute 24
  indexes into `derived.index_levels` and `derived.index_weights` (wave 14).
- **API.** `/hub/api/v1/us/indexes`, `/indexes/series`, `/indexes/{code}/constituents`
  and `/sectors/performance`.
- **Hub.** A `/indexes` page with:
  - a tracked-index strip;
  - a sector returns heatmap (1D to 1Y) with a cap/equal-weight toggle, ETF and S&P
    benchmarks;
  - a rebased comparison chart of our equal- and cap-weighted indexes against both ETFs;
  - top and bottom contributors and the constituents list.

## Non-goals

- Total-return indexes (dividends), float adjustment, or licensed FTSE Russell membership.
- Point-in-time history before the first snapshot (see ADR-0017's survivorship note).
- Intraday index levels.

## Acceptance criteria

- [x] `$SPX`-style symbols validate as `product_type = 'index'` and collect daily bars.
- [x] IWV holdings normalize into membership and GICS sectors (fixtures recorded from the
  live file, 2026-10-19 holdings).
- [x] Sector snapshots become effective-dated history in both sinks (conformance suite).
- [x] Equal- and cap-weight construction matches hand-computed cases (`libs/indexes`).
- [x] API and page tests cover series, sector performance, constituents and empty states.
- [ ] Wave 14 applied in production; first `indexes-us --rebuild` run reviewed.
- [ ] `r3k_iwv_cw` vs IWV daily-return correlation > 0.99 and `us_gics_*_cw` vs SPDRs
  > 0.95 on production data.
- [ ] A recorded Schwab `/quotes?fields=fundamental` capture added as the
  `fundamentals.shares_outstanding` fixture; `SchwabSharesOutstanding` registered.

## Rollout

1. `schema-migrator`: apply `wave_14_schema_indexes` (reviewed, forward-only).
2. `ingest-us` release (new `indexes-us` service, ishares provider, index symbol support).
3. Operator: update `/etc/factorlab/us-universe.yaml` from `deploy/us-universe.yaml`
   (provider `ishares`, `max_unvalidated`, `sector_provider`, `shares_outstanding`,
   benchmark `extra_symbols`), then restart `universe-us`. The daily scope grows to about
   2,600 symbols.
4. After the daily history backfill, run `factorlab-ingest-us indexes --rebuild` once.
5. `api` and `web` releases.

## Log

- 2026-10-21: created; implemented on the working branch with ADR-0017.
