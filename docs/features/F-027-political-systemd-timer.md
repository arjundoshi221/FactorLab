---
id: F-027
title: Run the political job from a systemd timer
status: backlog
priority: P2
components: [ingest-political, platform]
owner: unassigned
sprint: null
decisions: []
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-027 Run the political job from a systemd timer

## Problem

The political job is a host cron entry outside the deployer's model.

## Scope

- A systemd timer in the platform bundle; the deployer renders it for scheduled services.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] cron.d/factorlab-political is gone; the timer runs the same command.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
