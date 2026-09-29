---
id: F-025
title: API verifies the Cloudflare Access JWT
status: backlog
priority: P1
components: [api]
owner: unassigned
sprint: null
decisions: [ADR-0016]
links: [F-006]
created: 2026-09-30
updated: 2026-09-30
---

# F-025 API verifies the Cloudflare Access JWT

## Problem

Access is the hub's only authentication; if the edge is misconfigured (F-006) the API serves anyone.

## Scope

- Validate `Cf-Access-Jwt-Assertion` (audience, issuer, signature via the team certs) on /hub/api/* as defence in depth.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] Requests without a valid Access JWT get 401 from the API even when reached directly.

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
