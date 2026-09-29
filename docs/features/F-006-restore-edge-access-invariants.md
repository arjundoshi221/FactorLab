---
id: F-006
title: Restore the Cloudflare Access invariants on the hub hostname
status: planned
priority: P0
components: [platform, api]
owner: unassigned
sprint: 2026-S20
decisions: [ADR-0016]
links: []
created: 2026-09-30
updated: 2026-09-30
---

# F-006 Restore the Cloudflare Access invariants on the hub hostname

## Problem

On 2026-09-30 arjundoshi221.com resolved straight to the VPS (145.239.75.163) and served /hub/api/v1/* without Cloudflare Access. The hub has no in-app login, so its data was public.

## Scope

- DNS for every hub hostname is only the tunnel's proxied CNAME (no A/AAAA to the VPS).
- No public listener on 80/443 (Caddy disabled by install-edge-tunnel.sh; firewall drops inbound 80/443).
- Review the public proxy's access logs for the exposure window.

## Non-goals

- None recorded yet.

## Acceptance criteria

- [ ] `.github/workflows/edge-canary.yml` passes: every route redirects to *.cloudflareaccess.com.
- [ ] `dig +short arjundoshi221.com` returns Cloudflare addresses only.

## Rollout

Owner action in the Cloudflare dashboard and on the VPS (deploy/edge/README.md). Blocks F-007.

## Log

- 2026-09-30: seeded during the platform-v3 restructure (P8).
