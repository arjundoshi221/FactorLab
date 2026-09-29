---
id: F-003
title: Pipeline operations
status: backlog
priority: P2
components: [api, web, platform]
owner: unassigned
sprint: null
decisions: []
links: [F-026]
roadmap:
  version: V3
  summary: "Live run monitoring, failure diagnosis, and alert delivery."
created: 2026-09-30
updated: 2026-09-30
---

# F-003 Pipeline operations

## Problem

Failures are found by reading logs by hand; runs are not monitored live.

## Scope

- Live run monitoring from meta.ingestion_runs, failure diagnosis linked to logs by run_id, alert delivery.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] A failed or stuck run is visible in the hub within five minutes, with a link to its logs.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
