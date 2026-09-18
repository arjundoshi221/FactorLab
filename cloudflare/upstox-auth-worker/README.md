# FactorLab protected secrets and broker-auth Worker

This is the private side of FactorLab's no-domain Cloudflare deployment. It
runs on `workers.dev` and is protected as a whole by Cloudflare Access.

It provides the authenticated Upstox/Schwab management page and `POST
/v1/runtime-secrets` for the VPS secrets agent. It does not receive broker
OAuth callbacks: Access cannot exempt a single Worker path in the current
Cloudflare UI.

The public callback is a separate Worker at
`cloudflare/upstox-oauth-callback-worker`. Both Workers share the Cloudflare
Secrets Store and `RUNTIME_STATE` KV namespace.

Read the complete design and resume checklist in
[`docs/architecture/05-secrets-and-upstox-auth.md`](../../docs/architecture/05-secrets-and-upstox-auth.md).

## Local configuration

Copy `wrangler.example.toml` to ignored `wrangler.toml`, bind the existing KV
namespace and Secrets Store, and set:

```toml
UPSTOX_REDIRECT_URL = "https://factorlab-upstox-oauth-callback.YOUR_SUBDOMAIN.workers.dev/oauth/callback"
SCHWAB_CALLBACK_URL = "https://factorlab-upstox-oauth-callback.YOUR_SUBDOMAIN.workers.dev/oauth/schwab/callback"
CF_ACCESS_TEAM_DOMAIN = "YOUR_TEAM.cloudflareaccess.com"
CF_ACCESS_AUD = "YOUR_ACCESS_APPLICATION_AUD"
```

Deploy with:

```bash
npm run check
npx wrangler deploy
```

## Required Cloudflare bindings

```text
RUNTIME_STATE
UPSTOX_API_KEY
UPSTOX_API_SECRET
SCHWAB_APP_KEY
SCHWAB_APP_SECRET
TOKEN_ENCRYPTION_KEY
CLICKHOUSE_PASSWORD
CLICKHOUSE_PASSWORD_SHA256
EODHD_API_KEY
FACTORLAB_API_KEY
```

Only the Secrets Store bindings contain long-lived application credentials. The
VPS uses a separate Cloudflare Access service token saved as two root-only
bootstrap files.
