# Secrets and broker authentication [alpha]

## Purpose

FactorLab runs on a single VPS, but provider, database, and daily Upstox
credentials and broker tokens must not be committed to Git, placed in Compose files, or retained
in normal container filesystems. Cloudflare is the external secrets authority.

This document is the source of truth for secret architecture built on
2026-07-26. Current VPS topology and live status are tracked in
[`docs/VPS.md`](../VPS.md).

## Components

```text
Browser (Jai) -- Cloudflare Access --> factorlab-upstox-auth Worker
                                         | creates one-time OAuth state in KV
                                         v
                                  Upstox authorization page
                                         |
                                         v
                          public OAuth callback Worker
                          (validates one-time state; exchanges code)
                                         |
                                         v
                         encrypted daily Upstox token in Workers KV

VPS Cloudflare service token --> protected auth Worker /v1/runtime-secrets
                                         |
                                         v
                            secrets agent --> Docker tmpfs volumes
                                         |
             ClickHouse / India ingest / EODHD / future API containers
```

There are deliberately two Workers because Cloudflare Access for a Worker
applies to the whole Worker; the current UI cannot exempt only one path. Upstox
must be able to call the OAuth callback without a Cloudflare Access session.

| Component | URL / location | Trust role |
|---|---|---|
| Protected management Worker | `https://factorlab-upstox-auth.kairo-jai.workers.dev` | Browser management page and VPS runtime-secrets endpoint. Protected by Cloudflare Access. |
| Public callback Worker | `https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/callback` | Receives only Upstox OAuth callbacks. No secret-read endpoint. |
| Cloudflare Secrets Store | Cloudflare account | Long-lived provider and database credentials. |
| Workers KV `RUNTIME_STATE` | Cloudflare account | One-time OAuth states and AES-GCM encrypted daily Upstox token. |
| VPS bootstrap identity | `/etc/factorlab/identity` | Root-only Cloudflare Access service-token Client ID and Client Secret. |
| Container runtime files | Docker tmpfs named volumes | Rendered secret files; disappear on stop/reboot. |

## Stored secrets

Cloudflare Secrets Store contains:

```text
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

`TOKEN_ENCRYPTION_KEY` is a base64-encoded 32-byte AES-256 key. The daily
Upstox access token and Schwab access/refresh tokens are encrypted with AES-GCM
before they enter KV. Secrets Store
values are never returned to a browser.

The VPS retains only the Cloudflare Access service-token pair. This is an
unavoidable bootstrap credential for unattended startup; it is not an Upstox,
EODHD, or ClickHouse password.

## Upstox daily token flow

Generate or refresh the daily Upstox API token here:

**[Open FactorLab Upstox authentication](https://factorlab-upstox-auth.kairo-jai.workers.dev/)**

Cloudflare Access login is required. Choose **Refresh Upstox token**, complete
Upstox login/consent, then wait up to 60 seconds for the VPS secrets agent to
sync it. Current VPS deployment cannot sync until its Cloudflare Access service
token identity files are installed; see [`docs/VPS.md`](../VPS.md).

1. Open the protected management Worker and choose **Refresh Upstox token**.
2. The protected Worker creates a cryptographically random state and saves it
   in KV with a 10-minute TTL.
3. It redirects the browser to Upstox using the public callback URL below.
4. After login/consent, Upstox calls the public callback Worker with `code` and
   `state`.
5. The callback requires and consumes the one-time state, exchanges the code
   using the Upstox app credentials held in Secrets Store, validates the
   returned token against Upstox profile, encrypts it, and saves it in KV.
6. It redirects the browser back to the protected management Worker.
7. The VPS secrets agent obtains the token on its next poll (default 60s),
   writes it only to tmpfs, and the India client adopts it on its next safe
   request.

The Upstox access token is treated as expired at 03:30 IST. A new token must be
generated daily before Indian-market ingestion/trading begins.

## Schwab weekly authentication and automatic refresh

Authenticate or renew Schwab from the same protected management Worker using
**Authenticate Schwab**. The public callback route is:

```text
https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/schwab/callback
```

The callback validates a sealed ten-minute OAuth state shared by the two
Workers, exchanges the code, and encrypts both tokens in KV. The sealed state
does not depend on Workers KV propagation during the browser redirect. During
each VPS runtime-secret poll, the protected Worker refreshes an
access token that is within 60 seconds of expiry. Only the access token reaches
the US tmpfs volume; the app secret and refresh token stay in Cloudflare. When
the seven-day refresh-token lifetime ends, the management page reports
`reauth_required` and browser/MFA authentication must be repeated.

### Required Upstox Developer setting — pending

When access to the Upstox Developer dashboard is available, set and save this
exact Redirect URL:

```text
https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/callback
```

Do **not** use the protected management Worker URL as the redirect URL.

### `UDAPI100068`: invalid `client_id` or `redirect_uri`

This error occurs before Upstox login when OAuth request values do not exactly
match the Upstox Developer App.

1. Open [Upstox Developer Apps](https://account.upstox.com/developer/apps).
2. Open the app whose API key is stored as `UPSTOX_API_KEY`.
3. Set its Redirect URL to this exact value, with no trailing slash:

   ```text
   https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/callback
   ```

4. Save the app, then retry **Refresh Upstox token** from the protected Worker.
5. If the app cannot be edited, create a replacement app with this Redirect
   URL, then update both `UPSTOX_API_KEY` and `UPSTOX_API_SECRET` in Cloudflare
   Secrets Store before retrying.

The protected Worker and callback Worker already use the exact URL above. Do
not substitute the protected management URL
`https://factorlab-upstox-auth.kairo-jai.workers.dev/`.

## Cloudflare Access and VPS delivery

The management Worker is protected by the existing Cloudflare Access
application. Its Worker code also validates the signed `Cf-Access-Jwt-Assertion`
signature, audience, and expiry before returning `/v1/runtime-secrets`.

When resuming:

1. Zero Trust -> Access -> Service Tokens (or Service credentials) -> create
   `factorlab-vps-runtime`.
2. In the existing Access application for `factorlab-upstox-auth`, add an
   **Allow** or **Service Auth** policy that includes this service token.
3. Record its Client ID and Client Secret once. Do not add them to Cloudflare
   Secrets Store, Git, `production.env`, or Docker Compose.
4. On the VPS, create these root-owned `0600` files:

   ```text
   /etc/factorlab/identity/cloudflare-access-client-id
   /etc/factorlab/identity/cloudflare-access-client-secret
   ```

5. Set the protected Worker runtime endpoint in
   `/opt/factorlab/deploy/production.env`:

   ```text
   CLOUDFLARE_SECRETS_URL=https://factorlab-upstox-auth.kairo-jai.workers.dev/v1/runtime-secrets
   ```

The `cloudflare-secrets-agent` sends the pair as Cloudflare Access service-token
headers. It verifies the ClickHouse password/hash pair, then atomically renders
runtime files in Docker tmpfs volumes. It removes a stale Upstox token file when
the token is missing or expired.

## VPS filesystem layout

```text
/opt/factorlab/deploy                  Compose + non-secret configuration
/var/lib/factorlab/clickhouse          curated ClickHouse data (root disk)
/mnt/factorlab-data/clickhouse-raw     raw archive (50 GB extra disk)
/etc/factorlab/identity                root-only Cloudflare bootstrap identity
```

ClickHouse is intentionally not mounted on the extra 50 GB disk. The root disk
holds the current curated database; the extra disk is for lower-priority raw
archives. Docker volumes used for runtime credentials are tmpfs, not either
disk.

## Deployment status and verification

Already completed:

- Cloudflare Secrets Store values created.
- KV namespace created and bound to both Workers.
- `factorlab-upstox-auth` deployed and protected by Cloudflare Access.
- Public callback Worker deployed; `/health` returned HTTP 200 and an empty
  callback returned HTTP 400 as expected.
- TypeScript check passed for the protected Worker.
- `FACTORLAB_API_KEY` created and bound to the protected Worker.
- API-capable FactorLab image and production Compose uploaded to the VPS.

Still required:

- Set the Upstox Redirect URL above.
- Create and authorize the VPS Cloudflare Access service token.
- Put its two bootstrap values on the VPS.
- Install the VPS Cloudflare Access identity so the staged Compose dependency
  chain can start.
- Perform one real Upstox login and verify the India container receives a
  non-expired runtime token.

## Operational rules

- Never paste a secret into chat, source code, `.env`, `wrangler.toml`, Compose,
  screenshots, logs, or documentation.
- Rotate the VPS service token before its expiry; overwrite both root-only
  identity files and restart the secrets agent.
- If an Upstox app secret, ClickHouse password, or EODHD key changes, update the
  Cloudflare Secrets Store value; the agent picks it up at its next poll.
- A compromised VPS can use its service token while it remains valid. Restrict
  it to this one Access application, keep the identity directory root-only,
  rotate it, and do not expose ClickHouse publicly.
