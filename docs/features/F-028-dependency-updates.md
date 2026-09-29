---
id: F-028
title: Automated dependency updates (Renovate)
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

# F-028 Automated dependency updates (Renovate)

## Problem

uv.lock, npm locks, base-image digests and actions pins age silently.

## Scope

- Renovate with grouped weekly PRs; CI gates them.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Weekly update PRs pass ci-ok.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
