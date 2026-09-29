---
id: F-026
title: Alert on ERROR logs and failed runs
status: backlog
priority: P1
components: [platform, api]
owner: unassigned
sprint: null
decisions: [ADR-0014]
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-026 Alert on ERROR logs and failed runs

## Problem

Nobody is told when a pipeline fails; errors are found by reading logs.

## Scope

- Alert from `tools/read_logs.py errors` fingerprints and meta.ingestion_runs status.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] A failed run notifies the owner within 15 minutes.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
