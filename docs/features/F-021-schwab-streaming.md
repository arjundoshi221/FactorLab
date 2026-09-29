---
id: F-021
title: Live US data via Schwab streaming
status: backlog
priority: P2
components: [ingest-us]
owner: unassigned
sprint: null
decisions: [ADR-0002]
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-021 Live US data via Schwab streaming

## Problem

US intraday data arrives by polling; streaming was decided (ADR-0002) but not built.

## Scope

- A streaming provider feeding the same bar datasets as the REST tier.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Streamed bars match REST bars for one session.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
