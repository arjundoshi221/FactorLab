# FactorLab public broker OAuth callback Worker

This Worker is intentionally public because each broker redirects a browser to it
after authentication. It exposes only:

```text
GET /oauth/callback
GET /oauth/schwab/callback
GET /health
```

Each callback accepts a code only with a valid ten-minute OAuth state created
by the protected management Worker. Upstox uses a one-time state in shared KV;
Schwab uses a sealed state so its immediate callback does not depend on KV
propagation between Workers. The callback encrypts the daily Upstox access
token with AES-256-GCM and writes the encrypted record to KV. For Schwab it
encrypts both tokens; the refresh token stays in Cloudflare and only the
short-lived access token is returned to the VPS by the protected Worker. This
public Worker has no endpoint that reads or returns stored secrets.

The registered redirect URLs are:

```text
Upstox: https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/callback
Schwab: https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/schwab/callback
```

This Worker must share the protected Worker's `RUNTIME_STATE` KV namespace and
five Secrets Store bindings: `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`,
`SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, and `TOKEN_ENCRYPTION_KEY`.

See [`docs/architecture/05-secrets-and-upstox-auth.md`](../../docs/architecture/05-secrets-and-upstox-auth.md) for the complete architecture and operations runbook.
