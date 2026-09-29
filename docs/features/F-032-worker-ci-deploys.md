---
id: F-032
title: Decide on CI deploys for the Cloudflare Workers
status: idea
priority: P3
components: [upstox-auth-worker, upstox-oauth-callback-worker]
owner: unassigned
sprint: null
decisions: []
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-032 Decide on CI deploys for the Cloudflare Workers

## Problem

Worker releases are validated in CI but deployed by hand, so CI holds no Cloudflare credential.

## Scope

- Decide whether a scoped API token and environment variables for wrangler.toml are acceptable.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] A decision record (accepted or rejected).

## Rollout

Through the affected components' releases.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
