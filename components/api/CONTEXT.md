# api

> FastAPI read API and hub backend over ClickHouse v2; Cloudflare Access is its only authentication.

## Purpose

The `api` component serves the private hub's JSON API (`/hub/api/v1/*`), the bearer-key
research API (`/api/v1/*`) and `/health`. It only reads ClickHouse and the host's
Docker image snapshot; it writes no data. It is a deployable component (image
`ghcr.io/arjundoshi221/factorlab-api`, manifest [component.yaml](component.yaml)).

## Owns and does not own

- Owns: HTTP routes in [app.py](src/factorlab/components/api/app.py) and the routers and
  repositories beside it (catalog, schema map, India, US, political, Docker images), the
  response models, the bearer check in [auth.py](src/factorlab/components/api/auth.py),
  and the catalog descriptions (`catalog_descriptions.json`, `catalog_curated.json`).
- Does not own: the UI (`web`; the monolith image still bundles it), ClickHouse and the
  compose base (`platform`, [deploy/](../../deploy/)), schema DDL and readiness
  (`schema-migrator` / `libs/schema`), runtime secrets (`secrets-agent`), the tunnel and
  Access policy (Cloudflare dashboard, [deploy/edge/README.md](../../deploy/edge/README.md)).

## Entry points

- Console script `factorlab-api` ([`__main__.py`](src/factorlab/components/api/__main__.py)):
  uvicorn on `--host`/`--port` (default `0.0.0.0:8000` in the container), JSON logging,
  uvicorn access log off.
- Compose service `api` ([deploy/compose.yaml](deploy/compose.yaml)), published on
  `127.0.0.1:8000` only, `read_only`, `mem_limit: 512m`, network `backend`.
- Routes: `GET /health`; `/hub/api/v1/overview`, `/hub/api/v1/catalog`,
  `/hub/api/v1/catalog/pipelines[/{pipeline_id}/runs]`,
  `/hub/api/v1/catalog/tables/{name}[/rows|/rows.csv|/stats|/activity]`,
  `/hub/api/v1/schema-map[/v2]`, `/hub/api/v1/docker-images`,
  `/hub/api/v1/india/*`, `/hub/api/v1/us/*`, `/hub/api/v1/political/*`; and
  `/api/v1/india/*`, `/api/v1/us/*`, `/api/v1/political/*` (these require
  `Authorization: Bearer <FACTORLAB_API_KEY>`). FastAPI's `/docs` and `/openapi.json` are on.
- SPA fallbacks (`/`, `/data`, `/schema`, `/india`, `/us`, `/political`, `/docker-images`,
  `/roadmap`, ...) return `index.html` from `FACTORLAB_WEB_DIST`, or 503 if it is absent.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Bind address / port | `FACTORLAB_API_HOST`, `FACTORLAB_API_PORT` | `0.0.0.0`, `8000` | compose publishes loopback only |
| ClickHouse | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USERNAME` | compose: `clickhouse`, `8123`, `default`, `factorlab` | |
| ClickHouse password | secret `CLICKHOUSE_PASSWORD` | - | file in `FACTORLAB_SECRETS_DIR` |
| Bearer key for `/api/v1/*` | secret `FACTORLAB_API_KEY` | - | missing -> 503 on bearer routes |
| Secret directory | `FACTORLAB_SECRETS_DIR` | compose: `/run/secrets/app` | mounts the `political_runtime` tmpfs volume |
| Catalog previews | `FACTORLAB_CATALOG_PREVIEW`, `FACTORLAB_CATALOG_CSV`, `FACTORLAB_CATALOG_CSV_MAX_ROWS`, `FACTORLAB_CATALOG_PREVIEW_DENY` | `on`, `on`, `10000`, empty | set in `production.env`; applied on the next `api` recreate |
| Docker image snapshot | `FACTORLAB_DOCKER_IMAGES_SNAPSHOT` | `/run/docker-images/snapshot.v2.json`, else `snapshot.json` | host dir mounted read-only |
| UI bundle | `FACTORLAB_WEB_DIST` | `<FACTORLAB_HOME>/components/web/dist` | only the monolith image sets it |

Secret resolution is [`factorlab.core.secrets.get_secret`](../../libs/core/src/factorlab/core/secrets.py):
`<NAME>_FILE`, then `FACTORLAB_SECRETS_DIR/<NAME>`, then the environment.

## Data

Reads only. Tables include `meta.ingestion_runs`, `meta.expected_series`,
`meta.session_coverage`, `meta.source_status`, `meta.recovery_state`,
`meta.schema_migrations`, `market.bars`, `alt.political_*`, `ref.*` and `raw.archive`
metadata (the catalog reads every v2 table it lists). Catalog queries carry `readonly=2`,
`max_execution_time`, row/result/memory limits and `log_comment='hub-catalog:<kind>'`;
at most three preview queries run at once. Host path: `/var/lib/factorlab/docker-images`
(read-only, written by the platform's `factorlab-docker-images` timer).

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-clickhouse`, `factorlab-schema`, `factorlab-calendars`
  ([pyproject.toml](pyproject.toml)). Third-party: `fastapi`, `uvicorn[standard]`, `pandas`,
  `pydantic`, `pydantic-settings`, `exchange-calendars`, `tzdata`. `fastapi` and `uvicorn`
  may ship only in this image ([tools/verify_image.py](../../tools/verify_image.py)).
- Contracts: the response models consumed by `web` (`components/web/src/*Types.ts`); route
  paths the tunnel regex and `web` nginx expect (`/hub/api`, `/api`, `/health`, `/docs`,
  `/redoc`, `/openapi.json`); the snapshot schema written by
  [collect-docker-images.py](../../deploy/scripts/collect-docker-images.py); the catalog
  description files must cover the v2 schema ([tests/test_catalog_descriptions.py](../../tests/test_catalog_descriptions.py)).

## Observability

- Logs: `/var/log/factorlab/api/api.jsonl` (`component=api`, `service=api`). Logger
  `factorlab.api.access` writes one line per request with `method`, `route`, `status`,
  `duration_ms` and `request_id` (the `cf-ray` header); `/health` logs at DEBUG.
- Read with the `factorlab-logs` skill ([SKILL.md](../../.claude/skills/factorlab-logs/SKILL.md)):
  `uv run python tools/read_logs.py tail --component api --since 1h`.
- Health: image `HEALTHCHECK` runs `factorlab-healthcheck http http://127.0.0.1:8000/health`;
  the deployer waits for Docker `healthy`. Catalog cost: `system.query_log` rows with
  `log_comment LIKE 'hub-catalog%'` (see [deploy/README.md](../../deploy/README.md#data-catalog)).

## Tests

[tests/](tests/) (`test_api_*.py`), plus [tests/test_docker_images.py](../../tests/test_docker_images.py)
and [tests/test_catalog_descriptions.py](../../tests/test_catalog_descriptions.py):

```bash
uv run pytest components/api
```

## Release and rollback

`.\deploy\release.ps1 -Component api [-Bump patch|minor|major] [-DryRun]` runs
[tools/release.py](../../tools/release.py) `prepare`, then pushes tag `api/vX.Y.Z`, which
starts [component-release.yml](../../.github/workflows/component-release.yml): tag check,
workspace tests, image build/attest/verify, then [_deploy.yml](../../.github/workflows/_deploy.yml)
runs `deploy-component.sh api <version> <image@sha256> <bundle>` on the VPS. Rollback class
`auto`: a failed verification restores the previous fragment and pin
([factorlab_deploy.py](../../deploy/host/factorlab_deploy.py)). Manual rollback:
[component-rollback.yml](../../.github/workflows/component-rollback.yml) with `component=api`.
Until the rollout completes, production still runs the monolith image through `release/*`
tags ([release.yml](../../.github/workflows/release.yml), `deploy-release.sh`).

## Pitfalls

- **The hub has no in-app login.** Cloudflare Access via the tunnel is its only
  authentication; `/hub/api/v1/*` (including CSV export of any table) is unauthenticated in
  the app. Never publish port 8000 beyond `127.0.0.1` or add another route to the origin.
- `/api/v1/*` bearer clients also pass through Access; they need a Service Auth token or
  the SSH tunnel ([deploy/README.md](../../deploy/README.md#edge-access-cloudflare-tunnel-and-access)).
- The component image does not bundle the UI (no `FACTORLAB_WEB_DIST`), so `/` returns 503
  on it. Switch the tunnel catch-all to `web` before or with the first `api/v*` release.
- The FastAPI `version="0.1.0"` in `app.py` is not the release version; build info comes
  from `FACTORLAB_VERSION` / `FACTORLAB_COMMIT`.
- The API mounts `political_runtime`: the agent's healthcheck and this service both depend
  on `FACTORLAB_API_KEY` being rendered there.
- New tables need catalog descriptions, or `tests/test_catalog_descriptions.py` fails
  (`python scripts/generate_catalog_descriptions.py`).
