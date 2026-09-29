---
id: F-013
title: Apply wave 10 (source priorities) in production
status: backlog
priority: P1
components: [schema-migrator]
owner: unassigned
sprint: null
decisions: [ADR-0011, ADR-0015]
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-013 Apply wave 10 (source priorities) in production

## Problem

`ref.source_priorities` and `market.bars_best` are built (wave 10) but not applied in production.

## Scope

- Reviewed forward-only migration run of wave_10_schema_source_priorities and wave_10_views_source_priorities.
- Sync priorities from configs/ingestion/bindings.yaml.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] `factorlab-db migrate status` shows wave 10 applied; `tools/schema_checksums.py applied <id>` recorded.

## Rollout

Owner-approved production migration (never run by a release).

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
