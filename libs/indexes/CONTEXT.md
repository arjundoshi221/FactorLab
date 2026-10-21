# factorlab-indexes

> Pure index construction: equal- and cap-weighted indexes from daily closes, membership and shares timelines.

## Purpose

A library that turns daily closes, point-in-time membership/sector spans and
shares-outstanding observations into index levels and start-of-day constituent weights.
It also holds the US index catalogue. It does no I/O: the `ingest-us` `indexes-us`
service feeds it from ClickHouse, and the API reads its catalogue.
Methodology: [ADR-0017](../../docs/decisions/0017-sector-index-methodology.md).

## Owns and does not own

- Owns:
  - `IndexEngine` and `IndexDefinition` ([engine.py](src/factorlab/indexes/engine.py));
  - the quarterly third-Friday rebalance schedule;
  - `Timeline` / `Span` / `Observations` ([timeline.py](src/factorlab/indexes/timeline.py)),
    which apply the backfill clamp to the earliest snapshot;
  - the catalogue [catalog_us.yaml](src/factorlab/indexes/catalog_us.yaml), loaded by
    `load_catalog` ([catalog.py](src/factorlab/indexes/catalog.py)).
- Does not own:
  - reading inputs and writing `derived.index_*` (`factorlab-storage` `IndexStorage`);
  - scheduling (`ingest-us`);
  - HTTP (`api`).

## Entry points

None (library). Main calls:

- `load_catalog().definitions()` returns 2 composites plus ew/cw per sector (24 indexes).
- `IndexEngine(defs, base_date=..., members=..., shares=...).step(day, closes)` returns
  `list[IndexDay]`.

## Configuration and secrets

The catalogue YAML is package data: base date 2016-01-04, base value 1000, coverage gate
0.98, universe `r3k_iwv`, the sectors with their SPDR and Invesco equal-weight ETFs, and
the tracked series. No secrets, no environment reads.

## Data

None directly. Index codes are frozen strings written to `derived.index_levels`:
`r3k_iwv_ew`, `r3k_iwv_cw`, `us_gics_<sector>_ew`, `us_gics_<sector>_cw`.
`METHODOLOGY_VERSION` is `"1"`.

## Dependencies and contracts

- Workspace: `factorlab-core` (`LIBRARY_LAYERS["indexes"] = {"core"}`). Third-party:
  `pydantic`, `pyyaml`.
- Days must be fed in increasing order. A membership effective date applies at that
  day's close. Each day's contributions sum to the index return.

## Observability

None. The runner logs a summary per run and records `derived.computations`.

## Tests

`uv run pytest libs/indexes`. The hand-computed cases cover:

- equal-weight drift and the quarterly reset;
- deletion with renormalisation;
- cap-weight moves;
- the coverage gate;
- backfill clamping.

A further test checks that `deploy/us-universe.yaml` lists every benchmark symbol.

## Release and rollback

Never released alone. It ships in the `ingest-us` and `api` images. Changing the
methodology means bumping `METHODOLOGY_VERSION` and running `indexes --rebuild`.

## Pitfalls

- Equal-weight membership changes between rebalances only remove names; additions wait
  for the next quarterly rebalance.
- Cap weights use total shares (not float). Before the first shares snapshot they use the
  earliest one.
- The engine holds one close per listing plus weights. Keep inputs streamed by date; do
  not load a full price panel.
