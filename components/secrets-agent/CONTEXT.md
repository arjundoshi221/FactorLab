# secrets-agent

> Polls the Cloudflare auth Worker and renders runtime secrets into the per-consumer tmpfs volumes.

## Purpose

The `secrets-agent` component (compose service `cloudflare-secrets-agent`) is the only
path by which credentials reach production containers. Every
`CLOUDFLARE_SYNC_INTERVAL_SECONDS` it calls `POST /v1/runtime-secrets` on the
Access-protected [upstox-auth-worker](../../cloudflare/upstox-auth-worker/) with a
Cloudflare Access service token and atomically writes each value into the tmpfs volume of
the service that needs it. Image `ghcr.io/arjundoshi221/factorlab-secrets-agent`,
manifest [component.yaml](component.yaml).

## Owns and does not own

- Owns: [agent.py](src/factorlab/components/secrets_agent/agent.py) (fetch, validate,
  render, expire), the ClickHouse `users.d/runtime-users.xml` it generates, and the
  readiness marker `.agent-ready`.
- Does not own: the secret values and OAuth flows (Cloudflare Secrets Store, KV and the two
  Workers), the Access service-token files on the host (`/etc/factorlab/identity`, operator),
  the tmpfs volume definitions (`platform`, [compose.base.yml](../../deploy/compose.base.yml)),
  reading secrets in consumers ([`get_secret`](../../libs/core/src/factorlab/core/secrets.py)).

## Entry points

- Console script `factorlab-secrets-agent [--once]` (`--once`: one sync, exit 1 on failure).
- Compose service `cloudflare-secrets-agent` ([deploy/compose.yaml](deploy/compose.yaml)):
  root (`user: "0:0"`), `cap_drop: [ALL]`, `no-new-privileges`, `read_only`, network
  `backend`, no ports. ClickHouse and the IB Gateways wait for it to be `service_healthy`.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Worker URL | `CLOUDFLARE_SECRETS_URL` (`production.env`) | required (compose fails without it) | must be `https://` |
| Access token files | `CLOUDFLARE_ACCESS_CLIENT_ID_FILE`, `CLOUDFLARE_ACCESS_CLIENT_SECRET_FILE` | `/run/identity/cloudflare-access-client-id`, `.../cloudflare-access-client-secret` | secret names `CLOUDFLARE_ACCESS_CLIENT_ID`, `CLOUDFLARE_ACCESS_CLIENT_SECRET` |
| Identity dir | `FACTORLAB_IDENTITY_DIR` | `/etc/factorlab/identity` | root-only host dir, mounted read-only |
| Poll interval | `CLOUDFLARE_SYNC_INTERVAL_SECONDS` | `60` | minimum 15 |

## Data

No ClickHouse access. Files written (mode 0444, atomic replace) per volume:

| Volume (mount) | Consumer | Files |
|---|---|---|
| `clickhouse_runtime` (`/run/secrets/clickhouse`) | `clickhouse` (`users.d`) | `runtime-users.xml` (user `factorlab`, `password_sha256_hex`), `.agent-ready` |
| `india_runtime` (`/run/secrets/india`) | `ingest-india` | `CLICKHOUSE_PASSWORD`, `UPSTOX_ACCESS_TOKEN`, `UPSTOX_ACCESS_TOKEN.expires_at` |
| `us_runtime` (`/run/secrets/us`) | `universe-us`, `ingest-us` | `CLICKHOUSE_PASSWORD`, `EODHD_API_KEY`, `SCHWAB_ACCESS_TOKEN`, `SCHWAB_ACCESS_TOKEN.expires_at` |
| `political_runtime` (`/run/secrets/political`) | `api`, `bootstrap`, `ingest-political` | `CLICKHOUSE_PASSWORD`, `FACTORLAB_API_KEY` |
| `ibkr_paper_runtime`, `ibkr_live_runtime` | the Gateways | `TWS_PASSWORD`, `VNC_SERVER_PASSWORD` |
| `ibkr_client_runtime` (`/run/secrets/ibkr-client`) | `ibkr-snapshot` | `CLICKHOUSE_PASSWORD` |

Tokens are written only when the Worker reports `status == "valid"`; otherwise, and when
the local `expires_at` has passed, the files are removed.

## Dependencies and contracts

- Workspace: `factorlab-core`; third-party `requests` ([pyproject.toml](pyproject.toml)).
- Worker response contract (`version: 1`, at most 64 KiB): `secrets.CLICKHOUSE_PASSWORD`,
  `CLICKHOUSE_PASSWORD_SHA256` (must equal SHA-256 of the password), `FACTORLAB_API_KEY`
  (all required), optional `EODHD_API_KEY`, `UPSTOX_ACCESS_TOKEN`, `SCHWAB_ACCESS_TOKEN`,
  `IBKR_PAPER_PASSWORD`, `IBKR_LIVE_PASSWORD`, `IBKR_VNC_PASSWORD`; `upstox.status` /
  `expires_at`; `schwab.status` / `access_expires_at`. Change it together with
  [upstox-auth-worker/src/index.ts](../../cloudflare/upstox-auth-worker/src/index.ts).
- Design: [docs/architecture/05-secrets-and-upstox-auth.md](../../docs/architecture/05-secrets-and-upstox-auth.md).

## Observability

- Logs: `/var/log/factorlab/secrets-agent/cloudflare-secrets-agent.jsonl`. Each sync logs
  `Runtime secrets synchronized; token status: upstox=..., schwab=..., ibkr=...` or
  `Runtime secret synchronization failed: ...`:
  `uv run python tools/read_logs.py tail --component secrets-agent --since 1h`.
- Health kind `compose`: the fragment's healthcheck requires non-empty
  `/run/secrets/clickhouse/.agent-ready`, `runtime-users.xml`,
  `/run/secrets/india/CLICKHOUSE_PASSWORD` and `/run/secrets/political/FACTORLAB_API_KEY`.
- On the VPS: [diagnose-cloudflare-secrets.sh](../../deploy/scripts/diagnose-cloudflare-secrets.sh).

## Tests

[tests/test_cloudflare_secrets_agent.py](tests/test_cloudflare_secrets_agent.py):

```bash
uv run pytest components/secrets-agent
```

## Release and rollback

`.\deploy\release.ps1 -Component secrets-agent [-Bump ...] [-DryRun]` -> tag
`secrets-agent/vX.Y.Z` -> [component-release.yml](../../.github/workflows/component-release.yml)
-> `deploy-component.sh secrets-agent ...` via [_deploy.yml](../../.github/workflows/_deploy.yml).
Rollback class `auto`: a failed verification restores the previous fragment and pin
([factorlab_deploy.py](../../deploy/host/factorlab_deploy.py)); manual rollback with
[component-rollback.yml](../../.github/workflows/component-rollback.yml). The legacy monolith
path (`release/*`, `deploy-release.sh`, which starts the agent first) still runs production
until the rollout completes.

## Pitfalls

- It runs as root by design (`user_exception`: it reads the root-only identity files) with
  all capabilities dropped; it cannot `chown`, hence world-readable 0444 files. Keep each
  tmpfs volume mounted only into its own consumers.
- A failed sync keeps the last rendered files (except expired tokens) and retries next
  interval; the container stays up, so a Worker or Access outage shows only in the logs.
- The Upstox token is daily (expires 03:30 IST): an operator must log in on the Worker's
  management page each day.
- It renders nothing for `IBKR_CLIENT_ID`, although `ingest-broker` declares it.
- The file set checked by the healthcheck is a contract with the consumers; renaming a file
  or volume here breaks ClickHouse start-up and every writer.
