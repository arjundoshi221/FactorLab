---
id: F-031
title: Deploy windows in component manifests
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

# F-031 Deploy windows in component manifests

## Problem

Writers must deploy outside market hours; that is a convention, not a check.

## Scope

- `deploy_window` in component.yaml; the deployer refuses outside it unless forced.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] An ingest-india deploy during market hours is refused.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
