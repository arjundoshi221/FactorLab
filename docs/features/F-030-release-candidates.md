---
id: F-030
title: Release candidate tags (-rc.N)
status: backlog
priority: P3
components: [platform]
owner: unassigned
sprint: null
decisions: []
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-030 Release candidate tags (-rc.N)

## Problem

tools/release.py refuses pre-releases; staging a writer release is all-or-nothing.

## Scope

- `<c>/vX.Y.Z-rc.N` tags build and verify images without deploying.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] An rc tag produces a verified image and no deploy.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
