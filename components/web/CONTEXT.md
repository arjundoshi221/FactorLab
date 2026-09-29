# web

> The hub UI (React + Vite) as static files behind non-root nginx on 127.0.0.1:8080.

## Purpose

`web` is the FactorLab Data Hub single-page app: data catalog, schema explorer, pipelines,
India / US / political dashboards and the Docker images page. It is a `static` component:
the image `ghcr.io/arjundoshi221/factorlab-web` holds only the built bundle and nginx; all
data comes from the `api` component's `/hub/api/v1/*` routes on the same hostname.

## Owns and does not own

- Owns: everything under [src/](src/) (pages, components, shared fetch helpers, the
  TypeScript response types), [nginx/nginx.conf](nginx/nginx.conf), the
  [Dockerfile](Dockerfile) and the compose fragment [deploy/compose.yaml](deploy/compose.yaml).
- Does not own: the JSON API and its models (`api`), edge routing and the Access
  application (Cloudflare dashboard, see [deploy/edge/README.md](../../deploy/edge/README.md)),
  the compose base and the `edge` network (`platform`).

## Entry points

- Compose service `web`, profile `web`, network `edge` only (it cannot reach ClickHouse or
  the writers), published on `127.0.0.1:8080`, `read_only`, `mem_limit: 64m`, user 101.
- nginx: `GET /healthz` returns `{"status":"ok"}`; `/assets/` is cached immutable; every
  other path falls back to `index.html` (`no-cache`); `^/(hub/api|api)/` and
  `/health`, `/docs`, `/redoc`, `/openapi.json` return 404 so a misrouted API call never
  gets the SPA. `/version.json` carries `{"component":"web","version","commit"}`.
- Client routes (in [App.tsx](src/App.tsx)): `/`, `/data`, `/data/tables/<db>.<table>`,
  `/data/pipelines`, `/schema`, `/schema/v2`, `/india`, `/india/instruments/<uuid>`, `/us`,
  `/political`, `/docker-images`, `/roadmap`.
- npm scripts ([package.json](package.json)): `dev` (Vite on 127.0.0.1, proxies `/hub/api`
  to `http://127.0.0.1:8000`), `build` (`tsc -b && vite build`), `test` (`vitest run`).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Build version | `VITE_FACTORLAB_VERSION` | `0.0.0-dev` (shown as none) | set from the `VERSION` build arg |
| Build commit | `VITE_FACTORLAB_COMMIT` | `unknown` | set from the `COMMIT` build arg |
| Enable the service | `FACTORLAB_WEB_ENABLED` (in `production.env`) | off | turns on the `web` compose profile |
| Image pin | `FACTORLAB_WEB_IMAGE` | `ghcr.io/arjundoshi221/factorlab-web:unreleased` | written by the deployer to `state/images.env` |

No secrets (`secrets: []`). The browser never sends credentials of its own; Cloudflare
Access cookies do the authentication.

## Data

None in ClickHouse. It calls, among others, `/hub/api/v1/overview`, `/hub/api/v1/catalog*`,
`/hub/api/v1/schema-map`, `/hub/api/v1/docker-images`, `/hub/api/v1/us/*`,
`/hub/api/v1/india/*`, `/hub/api/v1/political/*`. Host path:
`/var/log/factorlab/web` (bind mount for nginx logs).

## Dependencies and contracts

- Runtime: `react`, `react-dom`, `@xyflow/react`, `elkjs`. Dev: Vite 7, TypeScript,
  Vitest, Testing Library, jsdom. Images: `node:22-bookworm-slim` (build) and
  `nginxinc/nginx-unprivileged:1.27-alpine` (runtime).
- Contract: the TypeScript types (`dataTypes.ts`, `indiaTypes.ts`, `usTypes.ts`,
  `politicalTypes.ts`, `schemaTypes.ts`) mirror the `api` response models; change both
  together. Errors surface the API's `detail` string ([shared/api.ts](src/shared/api.ts)).
- Edge contract: tunnel rule 1 sends `^/((hub/api|api)/|(health|docs|redoc|openapi\.json)$)`
  to `:8000`; the catch-all goes to `:8080` ([deploy/edge/README.md](../../deploy/edge/README.md)).

## Observability

- Logs: `/var/log/factorlab/web/access.jsonl` (JSON lines in the Python log shape:
  `component=web`, `service=web`, `logger=nginx.access`, `method`, `route` (no query
  string), `status`, `bytes`, `duration_s`, `request_id` = `cf-ray`) and
  `/var/log/factorlab/web/error.log`; errors also go to Docker's log.
- Read with the `factorlab-logs` skill: `uv run python tools/read_logs.py tail --component web`.
- Health: image `HEALTHCHECK` fetches `http://127.0.0.1:8080/healthz`; the deployer waits
  for Docker `healthy`.

## Tests

Vitest specs sit next to the code (`src/**/*.test.ts(x)`, setup in `src/testSetup.ts`):

```bash
cd components/web && npm ci && npm test && npm run build
```

## Release and rollback

`.\deploy\release.ps1 -Component web [-Bump ...] [-DryRun]` writes the version into
`package.json` and `package-lock.json` (a `static` unit) and pushes `web/vX.Y.Z`.
[component-release.yml](../../.github/workflows/component-release.yml) runs `npm ci`,
`npm test`, `npm run build`, builds and verifies the image (label and user checks), then
`deploy-component.sh web ...` via [_deploy.yml](../../.github/workflows/_deploy.yml).
Rollback class `auto` (restore the previous fragment and pin on failed verification);
manual rollback with [component-rollback.yml](../../.github/workflows/component-rollback.yml).
Rolling the UI back at the edge means pointing the tunnel catch-all at `:8000` again,
which works only while the API runs the monolith image (the legacy `release/*` path that
still runs production until the rollout completes).

## Pitfalls

- **The hub has no in-app login.** Cloudflare Access via the tunnel is its only
  authentication. Never publish `:8080` beyond `127.0.0.1` or route to it except through
  the tunnel.
- Enable `FACTORLAB_WEB_ENABLED=true` before the first release: the manifest does not mark
  `web` as profile-gated, so the deployer's preflight expects the service in the model.
- The CSP is `default-src 'self'` (inline styles allowed): no third-party scripts, fonts
  or API origins without changing both `nginx.conf` blocks.
- Security headers are repeated in `location /` because nginx drops inherited
  `add_header` when a location sets its own.
- `access.jsonl` is rotated with `copytruncate` (nginx keeps the file open); a few lines
  can be lost at rotation.
- `/india/instruments/<id>` expects a UUID; `/data/tables/<name>` must match
  `^[a-z_]+\.[a-z0-9_]+$`, as on the API side.
