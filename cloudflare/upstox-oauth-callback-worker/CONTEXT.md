# upstox-oauth-callback-worker

> Public Cloudflare Worker that receives the Upstox and Schwab OAuth redirects, exchanges the code and stores encrypted tokens in KV.

## Purpose

Brokers redirect the operator's browser here after login, so this Worker
(`name = "factorlab-upstox-oauth-callback"`, on `workers.dev`) is deliberately outside
Cloudflare Access. It accepts a code only with a valid state created by the protected
[upstox-auth-worker](../upstox-auth-worker/), exchanges it, encrypts the tokens with
AES-256-GCM and writes them to the shared `RUNTIME_STATE` KV. It never reads back or
returns stored secrets. It is a release unit of kind `worker`, deployed by hand. Design:
[docs/architecture/05-secrets-and-upstox-auth.md](../../docs/architecture/05-secrets-and-upstox-auth.md).

## Owns and does not own

- Owns: [src/index.ts](src/index.ts) (the two callbacks and `/health`),
  [wrangler.example.toml](wrangler.example.toml).
- Does not own: starting a login and creating the OAuth state, decrypting tokens, Schwab
  refresh and serving secrets to the VPS (all [upstox-auth-worker](../upstox-auth-worker/));
  the redirect URLs registered with Upstox and Schwab (broker developer portals).

## Entry points

| Route | Does |
|---|---|
| `GET /oauth/callback` | Upstox: consume one-time KV state `upstox/oauth-state/<state>`, exchange the code, validate the profile, write `upstox/current-token`, 303 to `MANAGEMENT_URL` |
| `GET /oauth/schwab/callback` | Schwab: accept a sealed state (10-minute window) or a legacy KV state `schwab/oauth-state/<state>`, exchange the code, write `schwab/current-token`, 303 to `MANAGEMENT_URL` |
| `GET /health` | `{"status":"ok"}` |

Anything else is 404. npm script ([package.json](package.json)): `check` (`tsc --noEmit`)
only; there is no `deploy` script here.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| KV | binding `RUNTIME_STATE` | - | the same namespace as the auth Worker |
| Secrets Store | `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `TOKEN_ENCRYPTION_KEY` | - | same store as the auth Worker |
| Vars | `MANAGEMENT_URL` | - | the auth Worker's `/` |
| Vars | `SCHWAB_CALLBACK_URL` | - | must equal the registered Schwab redirect and the auth Worker's `SCHWAB_CALLBACK_URL` |
| Upstox redirect | `CALLBACK_URL` constant in `src/index.ts` | `https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/callback` | hard-coded, not a var |

The real `wrangler.toml` is untracked (`cloudflare/**/wrangler.toml` is git-ignored).

## Data

KV writes: `upstox/current-token` (`version: 1`, `iv`, `ciphertext`, `issued_at`,
`expires_at` = next 03:30 IST, `user_id`, `user_name`); `schwab/current-token`
(encrypted `access_token` and `refresh_token`, `access_expires_at` from `expires_in`
(default 1800 s), `refresh_expires_at` = issue + 7 days). KV deletes: consumed state keys.
No ClickHouse access.

## Dependencies and contracts

- Dev dependencies only: `wrangler`, `typescript`, `@cloudflare/workers-types`;
  `compatibility_date = "2026-07-26"`.
- Shared with the auth Worker: KV key names, the record shapes, `TOKEN_ENCRYPTION_KEY`
  (base64, 32 bytes), and the sealed Schwab state format (12-byte IV + AES-GCM JSON with
  `provider: "schwab"`, `issued_at`, `nonce` of at least 32 characters). Change both Workers
  together.

## Observability

No FactorLab log files; errors go to `console.error` (Workers logs / `wrangler tail`).
Failures are visible to the operator as plain-text responses (`Invalid or expired OAuth
state.`, `... token exchange failed (<status>)`). Token status appears on the auth Worker's
page and, on the VPS, in the secrets agent's sync log line. Liveness: `GET /health`.

## Tests

No unit tests. CI type-checks and bundles:

```bash
cd cloudflare/upstox-oauth-callback-worker && npm ci && npm run check
npx wrangler deploy --dry-run --config wrangler.example.toml --outdir dist
```

## Release and rollback

`.\deploy\release.ps1 -Component upstox-oauth-callback-worker [-Bump ...] [-DryRun]` writes
the version to `package.json` / `package-lock.json` and pushes
`upstox-oauth-callback-worker/vX.Y.Z`. [component-release.yml](../../.github/workflows/component-release.yml)
runs [_release-worker.yml](../../.github/workflows/_release-worker.yml): `npm ci`,
`npm run check`, dry-run bundle uploaded as an artifact. Deploy by hand from the tag:
`npm ci && npm run check && npx wrangler deploy` with the local `wrangler.toml`. Roll back
by deploying the previous tag. There is no host rollback class.

## Pitfalls

- This Worker is public by design: never add a route that reads KV secrets or returns
  token material.
- The Upstox `redirect_uri` is the hard-coded `CALLBACK_URL`; if the Worker's hostname
  changes, update the constant, the auth Worker's `UPSTOX_REDIRECT_URL` and the Upstox app
  registration together.
- The legacy KV path for Schwab state is kept only for logins that began before the sealed
  format; do not build on it.
- The route handlers are returned without `await`, so an exception inside a callback escapes
  the `try` block and surfaces as the runtime's generic error, not `OAuth callback failed.`.
- A new Upstox login overwrites `upstox/current-token`; the VPS picks it up on the agent's
  next poll (60 s).
