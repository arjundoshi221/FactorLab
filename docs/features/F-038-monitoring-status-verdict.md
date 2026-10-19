---
id: F-038
title: One status verdict at the top of monitoring
status: review
priority: P1
components: [api, web, calendars]
owner: unassigned
sprint: null
decisions: []
links: [F-035, F-036, F-039, F-040]
created: 2026-10-19
updated: 2026-10-19
---

# F-038 One status verdict at the top of monitoring

## Problem

To answer "is everything working?", the operator had to read four service tiles, the source
banners, coverage cards and ingestion cards, then piece the answer together. Two false
signals made it worse:

- The IBKR snapshot had no pipeline entry. Its source status was attributed to `ingest-us`.
- A fixed 10-minute source rule and a fixed 2-hour snapshot rule flagged IBKR as stale
  overnight, at weekends and on holidays. The snapshot only runs at `IBKR_SNAPSHOT_TIMES`
  New York slots on XNYS sessions.
- After every run the daemon writes the source status `waiting` (the next slot). This
  showed as "not ready".

## Scope

- `GET /hub/api/v1/monitoring/summary` (`monitoring_summary.py`) returns `verdict`
  (`ok`, `attention`, `critical` or `unknown`), severity counts and `checks`. Each check
  has an area, severity, title, detail, time and a link to its evidence.
- Each gatherer is independent: services, sources, pipelines, coverage and broker
  snapshots. A failed read becomes an `unknown` check, never `ok`.
- The result is cached for 15s. Pipelines reuse the catalog's 60s cache.
- Severity rules:

  | Signal | Critical | Warning | Info |
  |---|---|---|---|
  | Monitor | watcher offline | | |
  | Services | `down` | late; unregistered sender; errors in the last hour | awaiting first report; deliberate stop |
  | Sources | `error`, `failed`, `auth_required` or `incomplete` | stale under that source's own schedule | |
  | Pipelines | `missing`; latest run of a non-per-symbol pipeline failed | other `attention` | |
  | Coverage | | missing bars, or active tickers with no check in the latest session | feed with no session checks (not measured, e.g. India) |
  | Broker snapshots | | active, non-simulated account missed its scheduled snapshot, or had none in 14 days | |

  Verdict order: critical, then attention, then unknown, then ok.
- Schedule-aware freshness (`source_health.py`, shared by the summary, the error inbox,
  `/catalog/pipelines`, portfolio snapshot freshness and the PnL stale-price flag):
  - A snapshot is stale when a slot at least 1 h old has no snapshot at or after it.
    Slots are found with `previous_scheduled_run` in `factorlab.calendars.us`, with 5 min
    tolerance.
  - The API reads the same `IBKR_SNAPSHOT_TIMES` as `ibkr-snapshot` (added to the api
    compose fragment).
  - `waiting` is a normal IBKR state.
  - Other sources keep the 10-minute rule.
- `ibkr_broker_snapshot` and `ibkr_broker_simulation` are catalogued under a "Broker"
  market and mapped to the `ibkr-snapshot` service.
- `SourceHealth` carries `service_id`, `stale`, `severe` and `problem`, so the web no
  longer re-derives those rules.
- `/monitoring` web changes:
  - The status panel replaces the four tiles and the top source banners.
  - It lists problems worst first with links. Notes and passing checks are collapsed.
  - The section navigation is sticky.
  - Service, pipeline and coverage cards have anchors (`#service-<id>`,
    `#pipeline-<id>`, `#coverage-<country>-<source>-<resolution>`). The linked card is
    outlined.
  - Coverage cards link to the ticker views.

## Non-goals

- No new collector behaviour or alerting. The summary is suitable for F-026 later.
- No change to how heartbeats are judged.
- The ingest-broker daemon overwrites a run's `incomplete`/`error` source status with
  `waiting` straight away. The summary still catches this through the pipeline's
  latest-run status. Fixing the order in the collector is separate work.

## Acceptance criteria

- [x] One request answers "is everything working", with a link from every problem to its evidence.
- [x] A failed gatherer, or an unreadable summary, never reads as "All systems working".
- [x] IBKR is judged against its slots, including the hourly production override, weekends and holidays.
- [x] IBKR runs and source status map to `ibkr-snapshot`, not `ingest-us`.
- [x] Focused API, calendars and web tests; frontend build.

## Rollout

Release `api` and `web` together. `api` needs the new `IBKR_SNAPSHOT_TIMES` value, which
the compose fragment takes from `production.env`. It must match `ibkr-snapshot`'s value;
production uses hourly slots. There is no schema change.

## Log

- 2026-10-19: implemented locally (summary endpoint, schedule-aware source health, IBKR
  pipeline mapping, status panel); not deployed.
