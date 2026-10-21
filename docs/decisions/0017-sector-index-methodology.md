---
id: ADR-0017
title: Track official US indexes from Schwab and construct GICS sector indexes over an IWV-based Russell 3000 proxy
status: proposed
date: 2026-10-21
status_note: Implemented on the branch; awaiting owner review, the wave 14 migration and the universe switch.
status_confirmed: false
---
# ADR-0017 — US index tracking and sector index methodology

## Context

The hub needs index levels and sector performance (F-042). Before this change, FactorLab
stored Schwab daily and 1-minute bars for single stocks only:

- The Schwab normalizer dropped `$`-prefixed index values (`$SPX`, `$COMPX`, ...).
- `ref.sectors` and `ref.securities.sector_id` existed but were never populated.
- There was no market-cap or shares-outstanding data.

The owner asked for sector indexes in two forms, **equal-weighted** and
**cap-weighted**, built over the **Russell 3000**. The data constraints are:

- **Index values.** Schwab serves official index values (`$SPX`, `$NDX`, `$DJI`, `$RUT`,
  `$COMPX`, `$VIX`) through `/quotes` and `/pricehistory`.
- **Sector constituents.** Schwab does not publish them.
- **The Russell roster.** The licensed FTSE Russell constituent file is not available. The
  free first-party substitute is BlackRock's IWV holdings CSV: about 2,580 listed equities,
  each with a GICS sector label and a fund weight. IWV samples the index, so it is a proxy,
  not the roster ([us-russell3000-daily.md](../operations/us-russell3000-daily.md)).
- **Shares outstanding.** Schwab quotes carry `fundamental.sharesOutstanding`
  ([schwab.md](../data-sources/us/schwab.md)), but only as of today. The earlier history
  would come from EDGAR, which is not yet collected.
- **Sector benchmarks.** State Street sector SPDRs (`XLK`...) and the Invesco equal-weight
  sector ETFs (`RSPT`...) are tradable comparisons for cap- and equal-weighted sector
  indexes.

## Decision

1. **Official indexes are bars.** Schwab `INDEX` quotes and instruments normalize to
   `product_type = 'index'` on the pseudo venue `US Index` (MIC `INDX`). That venue is
   seeded by `V2USStorage.seed_us_reference_data`. Their daily closes are ordinary
   `market.bars` rows. Benchmarks are listed in the universe config's `extra_symbols` and
   must always validate.
2. **Membership and sectors come from IWV holdings.** The `ishares` provider serves two
   datasets from one holdings file: `ref.universe_membership` (universe `r3k_iwv`, with
   fund weights) and the new `ref.security_sectors`.
   - It drops cash, futures, unlisted and OTC lines, rights, warrants, CVRs and escrows.
   - iShares sector labels map to GICS level-2 codes (`10` Energy ... `60` Real Estate).
   - Sector snapshots become effective-dated rows in `ref.security_sector_history` through
     the shared pure diff `diff_sectors`.
   - `universe-us` may drop up to `max_unvalidated` proxy members that Schwab cannot
     quote. Configured extras never drop.
3. **Shares come from a daily Schwab snapshot.** After each roster publish, `universe-us`
   writes `fundamentals.shares_outstanding` from batched `/quotes?fields=fundamental`.
4. **Indexes are computed by the `factorlab.indexes` library** (pure, depends only on core)
   and run by the `indexes-us` service:
   - **Universe.** Two whole-universe composites (`r3k_iwv_ew|cw`) and an ew and cw index
     per sector (`us_gics_<id>_ew|cw`). Base value 1000 on 2016-01-04.
   - **Returns.** Price-return only, from Schwab split-adjusted daily closes.
     `R = sum(w_i * r_i)`. A constituent without a close today contributes 0 and counts
     against coverage.
   - **Cap weight.** Weights reset every close to `close * shares_outstanding`, using the
     latest observation on or before the day, over the day's members. This is total
     shares, not float.
   - **Equal weight.** Weights reset to `1/N` at the close of the first session on or after
     the third Friday of March, June, September and December, and drift in between. Members
     that leave drop out, with the remaining weights renormalised. New members wait for the
     next rebalance.
   - **Membership timing.** Membership and sectors are taken as of each close, and an
     effective date applies at that day's close.
   - **Coverage gate.** A day whose priced start-of-day weight is below 98% is written
     with `complete = false`.
   - **Recompute policy.** Each run replays the full history in memory, streaming a year of
     closes at a time, and writes only the trailing 14 days (`--rebuild` writes all).
     Late data therefore heals itself.
5. **The catalogue is package data.** `factorlab.indexes/catalog_us.yaml` holds the tracked
   series, the sectors and their ETFs. The API and the runner read the same file. A test
   keeps `deploy/us-universe.yaml` `extra_symbols` in step with it.

## Consequences

- **Survivorship bias.** History before the first membership, sector and shares snapshot
  uses the earliest snapshot: today's roster, today's sectors, today's shares. Backfilled
  levels are therefore survivorship-biased, and old cap weights use current share counts.
  The UI states this. A licensed roster with history, and EDGAR share counts, would remove
  it.
- **Daily scope.** It grows from about 500 to about 2,600 symbols. That is roughly a
  50-minute nightly Schwab pass, and the account pool (F-041) helps.
- **Price return only.** Dividends are absent, so indexes will trail the total-return
  sector ETFs by roughly the dividend yield.
- **Unregistered shares source.** `SchwabSharesOutstanding` is not registered with the
  engine until a recorded production quote capture becomes its conformance fixture. Until
  then, `universe-us` calls it through an explicit binding.
- **Licensing.** IWV and SPDR holdings files are free to download but are not openly
  licensed. They are for internal research use, not redistribution.

## Alternatives considered

- **S&P 500 via SPDR holdings.** These are official GICS assignments, but the owner chose
  the broader Russell 3000.
- **Schwab `/instruments?projection=fundamental` for shares.** It returns the same count
  plus market cap, but one symbol per call in practice. Quotes batch 50 symbols.
- **Daily equal-weight rebalancing.** Simpler, but it overstates returns relative to an
  investable index.
- **A charting dependency (`lightweight-charts`).** Rejected. The hub's CSP and existing
  pages favour hand-rolled SVG, and one comparison chart does not justify a dependency.
