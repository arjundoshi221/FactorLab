---
id: F-029
title: GHCR image retention
status: backlog
priority: P2
components: [platform]
owner: unassigned
sprint: null
decisions: []
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-029 GHCR image retention

## Problem

Per-component images accumulate in GHCR.

## Scope

- Keep tagged releases; delete untagged images older than 30 days.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Retention job runs weekly.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
