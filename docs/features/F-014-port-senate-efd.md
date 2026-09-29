---
id: F-014
title: Re-port Senate eFD periodic transaction reports as a provider
status: backlog
priority: P2
components: [ingest-political]
owner: unassigned
sprint: null
decisions: [ADR-0011]
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-014 Re-port Senate eFD periodic transaction reports as a provider

## Problem

Senate eFD periodic transaction reports ingestion was deleted in the restructure (see tag archive/pre-restructure) and has no provider yet.

## Scope

- `fl-scaffold new-provider` for it, recorded fixtures (07 §13.1), conformance tests, a shadow binding.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Conformance suite green on recorded captures; shadow binding writes to the political sinks.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
