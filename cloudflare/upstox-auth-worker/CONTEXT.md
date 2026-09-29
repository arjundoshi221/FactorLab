# upstox-auth-worker

> Access-protected Cloudflare Worker: the broker-auth management page and `POST /v1/runtime-secrets` for the VPS secrets agent.

## Purpose

This Worker (`name = "factorlab-upstox-auth"`, on `workers.dev`) is the private side of
FactorLab's secrets design. Operators use its page to start the daily Upstox login and
the Schwab login; the VPS `secrets-agent` calls it with an Access service token to fetch
every runtime secret. It is a release unit of kind `worker`: validated and bundled in CI,
deployed by hand. Design: [docs/architecture/05-secrets-and-upstox-auth.md](../../docs/architecture/05-secrets-and-upstox-auth.md).

## Owns and does not own

- Owns: [src/index.ts](src/index.ts) (routing, Access JWT verification, OAuth start, token
  decryption, Schwab refresh, the runtime-secret response), [wrangler.example.toml](wrangler.example.toml).
- Does not own: the OAuth callbacks and first token write
  ([upstox-oauth-callback-worker](../upstox-oauth-callback-worker/)), the Access application
  and service token (Cloudflare dashboard), secret values (Secrets Store), rendering on the
  VPS ([secrets-agent](../../components/secrets-agent/)).

## Entry points

| Route | Auth | Does |
|---|---|---|
| `GET /` | Access JWT | management page (Upstox and Schwab status, login buttons) |
| `GET /api/login`, `GET /api/upstox/login` | Access JWT | 302 to the Upstox dialog; one-time state in KV (600 s) |
| `GET /api/schwab/login` | Access JWT | 302 to Schwab authorize; sealed AES-GCM state (no KV) |
| `GET /api/status` | Access JWT | token status JSON (no secret values) |
| `POST /v1/runtime-secrets` | Access JWT (service token) | the runtime-secret bundle; other methods 405 |
| `GET /health` | none | `{"status":"ok"}` |

npm scripts ([package.json](package.json)): `check` (`tsc --noEmit`), `deploy`
(`wrangler deploy`), `dev` (`wrangler dev`).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| KV | binding `RUNTIME_STATE` | - | shared with the callback Worker |
| Secrets Store | `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `TOKEN_ENCRYPTION_KEY`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_PASSWORD_SHA256`, `EODHD_API_KEY`, `FACTORLAB_API_KEY` | - | required bindings |
| Optional secrets | `IBKR_PAPER_PASSWORD`, `IBKR_LIVE_PASSWORD`, `IBKR_VNC_PASSWORD` | null | commented out in the example |
| Vars | `UPSTOX_REDIRECT_URL`, `SCHWAB_CALLBACK_URL` | - | the callback Worker's `/oauth/callback`, `/oauth/schwab/callback` |
| Vars | `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` | - | JWT issuer certs and audience; empty -> every protected route 403 |

The real `wrangler.toml` is untracked (`cloudflare/**/wrangler.toml` is git-ignored).

## Data

KV keys: `upstox/current-token` (AES-GCM encrypted token, `expires_at` = next 03:30 IST),
`upstox/oauth-state/<state>` (TTL 600 s), `schwab/current-token` (encrypted access and
refresh tokens; refreshed and rewritten here when the access token is within 5 minutes of
expiry). No ClickHouse access.

## Dependencies and contracts

- Dev dependencies only: `wrangler`, `typescript`, `@cloudflare/workers-types`;
  `compatibility_date = "2026-07-26"`.
- Runtime-secret response (consumed by
  [agent.py](../../components/secrets-agent/src/factorlab/components/secrets_agent/agent.py)):
  `version: 1`, `generated_at`, `secrets.{CLICKHOUSE_PASSWORD, CLICKHOUSE_PASSWORD_SHA256,
  UPSTOX_ACCESS_TOKEN, SCHWAB_ACCESS_TOKEN, EODHD_API_KEY, FACTORLAB_API_KEY,
  IBKR_PAPER_PASSWORD, IBKR_LIVE_PASSWORD, IBKR_VNC_PASSWORD}`, `upstox.{status,
  issued_at, expires_at, user_id}`, `schwab.{status, issued_at, access_expires_at,
  refresh_expires_at}`. Statuses: Upstox `valid` / `expired` / `missing`; Schwab `valid` /
  `reauth_required` / `refresh_failed` / `missing`.
- The KV record shapes and `TOKEN_ENCRYPTION_KEY` (base64, 32 bytes) are shared with the
  callback Worker; change both together.

## Observability

No FactorLab log files: errors go to `console.error` (Workers logs / `wrangler tail`),
without secret values. The VPS side shows each sync in
`/var/log/factorlab/secrets-agent/cloudflare-secrets-agent.jsonl` (`factorlab-logs` skill).
Liveness: `GET /health`.

## Tests

No unit tests. CI (`ci.yml`, `_release-worker.yml`) type-checks and bundles:

```bash
cd cloudflare/upstox-auth-worker && npm ci && npm run check
npx wrangler deploy --dry-run --config wrangler.example.toml --outdir dist
```

## Release and rollback

`.\deploy\release.ps1 -Component upstox-auth-worker [-Bump ...] [-DryRun]` writes the
version to `package.json` / `package-lock.json` and pushes `upstox-auth-worker/vX.Y.Z`.
[component-release.yml](../../.github/workflows/component-release.yml) hands it to
[_release-worker.yml](../../.github/workflows/_release-worker.yml): `npm ci`, `npm run check`,
a dry-run bundle uploaded as an artifact (30 days). Nothing is deployed from CI (it holds
no Cloudflare credential). Deploy by hand: `git checkout upstox-auth-worker/vX.Y.Z`, then
`npm ci && npm run check && npx wrangler deploy` with the local `wrangler.toml`. Roll back
by deploying the previous tag the same way. There is no host rollback class.

## Pitfalls

- Every route except `/health` and `/v1/runtime-secrets` checks the Access JWT itself;
  `/v1/runtime-secrets` checks it inside its handler. Keep the Access application covering
  the whole hostname as well: do not rely on one layer.
- The callback cannot live here: Access cannot exempt a single Worker path, so the callback
  Worker is separate and public.
- A missing required Secrets Store binding makes `/v1/runtime-secrets` return 500, and the
  agent then keeps the last rendered files.
- Changing the response shape breaks the agent's validation; deploy the agent change first
  or keep the change additive.
