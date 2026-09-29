---
id: F-033
title: Shrink the type baseline and raise coverage floors
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

# F-033 Shrink the type baseline and raise coverage floors

## Problem

927 basedpyright findings are baselined; total coverage is 77.7% (EDGAR 43.7%).

## Scope

- Fix baselined findings member by member; raise CI floors as coverage improves.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] The baseline shrinks each sprint; floors never go down.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
