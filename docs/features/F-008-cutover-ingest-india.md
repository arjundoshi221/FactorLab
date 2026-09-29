---
id: F-008
title: Cut ingest-india over to the ingestion engine
status: backlog
priority: P1
components: [ingest-india]
owner: unassigned
sprint: null
decisions: [ADR-0011]
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-008 Cut ingest-india over to the ingestion engine

## Problem

India 1-minute bars (`india_intraday_1min`) still run through legacy/ (Upstox).

## Scope

- Shadow parity for one full session against the legacy writer.
- Promote the binding from shadow, swap the compose command to the engine, soak one week.
- `git rm -r` the legacy/ package and shrink tests/architecture/legacy_budget.txt.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Parity report shows identical rows (or explained differences) for one session.
- [ ] legacy/ is deleted and legacy_budget.txt shrank.

## Rollout

Release the component (writer class; bump data_contract only if written data changes).

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
