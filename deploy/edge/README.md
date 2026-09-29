# Edge routing: Cloudflare Tunnel and Access

The hub has no in-app login. **Cloudflare Access is its only authentication**, and
the only path to the origin is the outbound Cloudflare Tunnel
(`factorlab-cloudflared.service`). The tunnel's ingress rules and the Access
application live in the Cloudflare dashboard, not in this repository. This page is
the expected configuration; `.github/workflows/edge-canary.yml` checks it from
outside every day.

## Invariants

1. DNS for the hub hostnames is only the tunnel's proxied CNAME. There are **no
   A or AAAA records** pointing at the VPS, and `dig +short arjundoshi221.com`
   returns Cloudflare addresses.
2. The VPS opens no public 80/443/8000/8080. Every published container port is
   bound to `127.0.0.1`: the fragment lint and the host deployer enforce this.
   No public reverse proxy (Caddy) runs.
3. One Access application (self-hosted, `FactorLab Hub`) covers each whole
   hostname. It has no Bypass policies and no path-scoped applications that could
   shadow it.
4. Unauthenticated requests to any path are redirected to `*.cloudflareaccess.com`.
   The canary checks `/`, `/docker-images`, `/hub/api/v1/*`, `/api/v1/`, `/health`,
   `/docs` and `/openapi.json` on every hostname.

## Tunnel ingress (public hostnames), in order

| # | Hostname | Path (regex) | Service |
|---|---|---|---|
| 1 | `arjundoshi221.com` | `^/((hub/api\|api)/\|(health\|docs\|redoc\|openapi\.json)$)` | `http://127.0.0.1:8000` (api) |
| 2 | `arjundoshi221.com` | *(empty: everything else)* | `http://127.0.0.1:8080` (web) |

Repeat both rules for `www.arjundoshi221.com` if that hostname is used.

- **Until `web` is released**, keep a single rule sending everything to `:8000`.
  The API still serves the UI.
- **Switching to web.** Enable the `web` profile (`FACTORLAB_WEB_ENABLED=true` in
  `production.env`), release `web/v1.0.0`, and confirm that
  `curl -s 127.0.0.1:8080/healthz` works on the VPS. Then add rule 1 above the
  existing rule and point the catch-all at `:8080`.
- **Rolling web back** means pointing the catch-all at `:8000` again. The API keeps
  serving the UI until the monolith is retired (rollout R4).
- The web container answers `/hub/api/*` with 404. A misrouted API call therefore
  never receives the SPA's HTML.

## Checking

```bash
dig +short arjundoshi221.com                         # Cloudflare addresses only
curl -sI https://arjundoshi221.com/hub/api/v1/overview | head -3   # 302 to *.cloudflareaccess.com
sudo ss -tlnp | grep -vE '127\.0\.0\.1|\[::1\]'       # on the VPS: no public listeners (except SSH)
```

If anything answers with 200 without Access, treat it as an incident. The origin is
exposed until DNS points only at the tunnel and the public listener is gone.
`install-edge-tunnel.sh` disables Caddy, and the VPS firewall should drop inbound
80/443.
