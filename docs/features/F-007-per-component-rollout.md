---
id: F-007
title: Roll out per-component releases (R0–R5)
status: planned
priority: P0
components: [platform, api, web, secrets-agent, schema-migrator, ingest-india, ingest-us, ingest-political, ingest-broker]
owner: unassigned
sprint: 2026-S20
decisions: [ADR-0013, ADR-0015]
links: [F-006]
created: 2026-09-30
updated: 2026-09-30
---

# F-007 Roll out per-component releases (R0–R5)

## Problem

The restructure branch is complete, but production still runs the monolith image and release path.

## Scope

- R0 merge restructure/platform-v3 with a merge commit; R1 one monolith release; R2 platform/v1.0.0 (sudoers entry, log-reader key); R3 components one at a time (web, api, secrets-agent, schema-migrator, ingest-broker, ingest-political, ingest-us, ingest-india; writers outside market hours); R4 retire the monolith; R5 ClickHouse logging maintenance window.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Every service runs its component image at a `<c>/vX.Y.Z` tag; `release/*` tags and release.yml are gone.
- [ ] A rollback was exercised on api and web.
- [ ] `tools/read_logs.py list` shows every component at its version.

## Rollout

docs/operations/rollout.md is the step-by-step checklist.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
