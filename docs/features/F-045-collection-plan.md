---
id: F-045
title: Show each ingestion service's universe, resolution and cadence in the hub
status: review
priority: P1
components: [api, web, ingest-us, ingest-india, ingest-political, ingest-broker, ingest]
owner: unassigned
sprint: null
decisions: []
links: [F-038, F-035]
created: 2026-10-30
updated: 2026-10-30
---

# F-045 Collection plan

## Problem

No single place shows which universe each running ingestion service collects, at what resolution
and how often. The answer is spread across several places:

- manifest commands (`--universe us_listed_equities`, `${UPSTOX_UNIVERSE:-full_nse_eq}`)
- the operator-edited `/etc/factorlab/us-universe.yaml`
- env overrides such as `US_MINUTE_TIER_SIZE` and the hourly production `IBKR_SNAPSHOT_TIMES`
- code constants such as `us_liquid_250`
- prose on the Pipelines page that drifts from all of the above

Two examples of the drift:

- The US live 1-minute sweep was described as covering "every configured US equity". It covers only
  the liquid minute tier, up to 250 names.
- Live and minute-recovery runs recorded `universe_id = 'pilot'`, a leftover default.

## Scope

- **Collectors record config:** each collector records the settings a run actually used as
  `metadata_json["config"]` on every `meta.ingestion_runs` row. `ingestion_run(..., config=)` and
  `run_provider(..., config=)` take the config, and `factorlab.ingest.run_config` builds it. There is no
  schema change.
  - ingest-us collector: minute universe, tier size, live interval, Schwab pacing.
  - universe-us: provider, indexes, refresh interval, `max_unvalidated`, sector provider, extras.
  - futures-us: roots, contracts, poll interval. Its runs now record `universe_id = 'us_futures'`.
  - ingest-india: mode, quote and futures cadence, batch size, recovery, UTC window.
  - ingest-political: PTR limit, year, congress.
  - ibkr-snapshot: modes, NY slots.
- **US universe labels fixed:** US live and minute-recovery runs record `us_liquid_250`. Named-universe
  runs record their own universe instead of `pilot`.
- **Catalogue declares the plan:** `PipelineInfo` gains `service`, `universe` and `resolution`, and
  `monitoring_errors.PIPELINE_SERVICES` is derived from it.
- **New endpoint:** `GET /hub/api/v1/monitoring/collection-plan` groups pipelines by component → service.
  Each pipeline shows:
  - declared universe, resolution and schedule
  - Pipelines-card status and last run
  - the latest run's universe and reported config
  - median gap between runs over 24 hours
  - active `meta.expected_series` members per resolution
  - flags: `universe_mismatch`, `several_universes`, `config_unreported`

  The endpoint is read-only, uses 5 s deadlines and is cached for 60 s. A failed observation is listed in
  `unavailable`.
- **Hub section:** a "Collection plan" section on the Monitoring page.

## Non-goals

- Changing what is collected, or any cadence.
- A schema migration or a new table.
- Adding `component` to `monitor-services.yaml`. The API keeps a service → component map, and a test
  checks it against the manifests.

## Acceptance criteria

- [x] Every ingestion pipeline appears under its component and service, with a declared universe where runs carry one.
- [x] The collectors record their effective config on each run, and tests assert it.
- [x] Drift between the reported and declared universe is flagged, and failed reads never look clean.
- [ ] After the collector releases, the hub shows production values: `us_liquid_250` with 250 minute members, and hourly IBKR slots.

## Rollout

1. Release `api` and `web`. The section works at once and shows `config_unreported` until each
   collector is released.
2. Release `ingest-us`, `ingest-india`, `ingest-broker` and `ingest-political`. This is an additive
   metadata key plus corrected `universe_id` labels, so `data_contract` stays 1.

## Log

- 2026-10-30: created and built (review); releases pending.
