---
id: F-001
title: Data health
status: in-progress
priority: P1
components: [api, web]
owner: unassigned
sprint: null
decisions: []
links: []
roadmap:
  version: V1
  summary: "Table inventory, coverage dates, and schedule-aware freshness."
created: 2026-09-30
updated: 2026-09-30
---

# F-001 Data health

## Problem

Operators need to see, per table and pipeline, whether today's data is complete and fresh.

## Scope

- Table inventory, coverage dates and schedule-aware freshness in the hub.
- Per-component versions and running services (Docker images page).

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Every production table shows first/last data, today's rows and a status with a reason.
- [ ] The hub shows each component's deployed version.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
