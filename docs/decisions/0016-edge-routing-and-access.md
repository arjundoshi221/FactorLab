---
id: ADR-0016
title: Single hub hostname behind Cloudflare Tunnel and one Access application
status: accepted
date: 2026-09-29
status_note: The decision stands, but on 2026-09-30 production did not meet its invariants (see Consequences); restoring them is feature F-006 and blocks the per-component rollout.
status_confirmed: false
---
# ADR-0016 — Single hub hostname behind Cloudflare Tunnel and one Access application

## Context

- The hub (the web UI and the `/hub/api/*` backend) has no in-app login. The api image
  describes Cloudflare Access as its only authentication. Anything that reaches the origin
  without passing through Access therefore reaches the data.
- The VPS used to publish the hub through a public Caddy reverse proxy on 80/443
  (`deploy/caddy/Caddyfile`, proxying to `127.0.0.1:8000`), so the origin was reachable
  directly as well as through Cloudflare.
- The restructure splits the UI into its own `web` component (nginx on `:8080`), while
  `api` stays on `:8000`. The edge has to send each path to the right one without adding a
  second hostname or a second place where authentication is decided.
- The tunnel ingress and the Access application live in the Cloudflare dashboard, not in
  this repository, so drift there is invisible to code review.

## Decision

- **One hostname, one path to the origin.** The hub is served at `arjundoshi221.com` (and
  `www.arjundoshi221.com` if used) only through the outbound Cloudflare Tunnel
  (`deploy/systemd/factorlab-cloudflared.service`). DNS for the hub hostnames is only the
  tunnel's proxied CNAME, with no A or AAAA records pointing at the VPS.
- **One Access application, no bypass.** A single self-hosted Access application
  (`FactorLab Hub`) covers each whole hostname. It has no Bypass policies, and no path-scoped
  applications that could shadow it. An unauthenticated request to any path is redirected
  to `*.cloudflareaccess.com`.
- **Path split at the tunnel**, as specified in [`deploy/edge/README.md`](../../deploy/edge/README.md):
  1. `^/((hub/api|api)/|(health|docs|redoc|openapi\.json)$)` goes to `http://127.0.0.1:8000`
     (api).
  2. Everything else goes to `http://127.0.0.1:8080` (web).

  Until `web` is released, a single rule sends everything to `:8000`, and the API still
  serves the UI. Rolling web back means pointing the catch-all at `:8000` again. The web
  container answers `/hub/api/*` with 404, so a misrouted API call never receives the SPA's
  HTML.
- **Loopback-only ports.** Every published container port binds `127.0.0.1`. The compose
  fragment lint (`tools/components.py`) and the host deployer
  ([ADR-0015](0015-host-deployer-and-rollback-classes.md)) both refuse anything else. No public
  reverse proxy runs: `install-edge-tunnel.sh` disables Caddy, and the VPS firewall should
  drop inbound 80/443.
- **Checked from outside, daily.** [`edge-canary.yml`](../../.github/workflows/edge-canary.yml)
  runs every day at 06:17 UTC and on demand. It requests `/`, `/docker-images`,
  `/hub/api/v1/overview`, `/hub/api/v1/docker-images`, `/api/v1/`, `/health`, `/docs` and
  `/openapi.json` on every hub hostname without credentials. It passes only on a redirect to
  `*.cloudflareaccess.com` or a 401/403 from Access. Any other answer fails the run, and
  GitHub notifies the repository owners.

## Consequences

- Authentication is decided in exactly one place, and there is one hostname to reason
  about. Moving the UI from api to web is an ordered change of two tunnel rules, with a
  one-rule rollback.
- The origin is only as safe as the dashboard configuration and DNS. Neither is in the
  repository, which is why the invariants are written down in `deploy/edge/README.md` and
  the canary checks them from outside.
- **The invariants did not hold on 2026-09-30.** An external check found `arjundoshi221.com`
  resolving directly to the VPS rather than to Cloudflare. It also found the VPS serving the
  hub without Access: an unauthenticated `GET /hub/api/v1/overview` returned 200 with JSON,
  and `/health` answered. The responses carried no Cloudflare headers, which is consistent
  with the old public proxy still serving 443. This is exactly the failure the canary exists
  to catch. It had not caught it, because the canary lives on this branch, and GitHub runs
  scheduled workflows only from the default branch.
- The invariants in `deploy/edge/README.md` must be restored, and the canary must pass,
  before the per-component rollout continues (feature F-006, which blocks F-007). The public
  proxy's access logs for the exposure window should be reviewed.
- Nothing behind the edge authenticates a request. Loopback-only ports narrow the exposure
  but do not replace Access. Verifying the `Cf-Access-Jwt-Assertion` header in the API, as
  defence in depth, is backlog feature F-025.

## Alternatives considered

- **Separate hostnames for UI and API** (for example `api.` and `hub.`). Rejected: two
  hostnames need two Access scopes that can drift apart, and cross-origin calls from the
  SPA.
- **Path-scoped Access applications or Bypass rules** (for example public `/health` or
  `/docs`). Rejected: a narrower application or a Bypass can shadow the hostname-wide one,
  which is how a public path appears.
- **A public reverse proxy (Caddy) with in-app authentication.** Rejected: it keeps a public
  listener on the origin and adds a login system to build and maintain.
- **Routing inside nginx (web proxying `/hub/api` to api).** Rejected: web would then have to
  run whenever api does, and rolling web back would also break the API. The tunnel split
  keeps each component independently releasable.
- **Rely on the Cloudflare dashboard alone, without a canary.** Rejected: configuration
  there changes without review, and the 2026-09-30 finding shows the cost of not checking.
