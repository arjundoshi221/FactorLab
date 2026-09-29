# ingest-us

> US universe resolution and Schwab daily / 1-minute bars into ClickHouse v2 (two services, one image).

## Purpose

`ingest-us` resolves the configured US equity universe (`universe-us`) and collects prices
for it (`ingest-us`): live sweeps plus resumable daily and 1-minute recovery, with every
provider response archived to `raw.archive`. It is a writer component (image
`ghcr.io/arjundoshi221/factorlab-ingest-us`, manifest [component.yaml](component.yaml)).

## Owns and does not own

- Owns: the `factorlab-ingest-us` CLI ([cli.py](src/factorlab/components/ingest_us/cli.py)),
  the collector and universe daemons under [legacy/](src/factorlab/components/ingest_us/legacy/)
  (Schwab market client, EODHD client, universe resolvers `github_csv` / `eodhd` / `schwab`),
  and the providers the image ships ([providers.py](src/factorlab/components/ingest_us/providers.py):
  `schwab`, `eodhd`, `github_csv`, `edgar`).
- Does not own: Schwab OAuth and token refresh (the Cloudflare Workers), token delivery
  (`secrets-agent`), the ClickHouse writers ([`V2USStorage`](../../libs/storage/src/factorlab/storage/v2_us.py)),
  the schema (`schema-migrator`), the host config file `/etc/factorlab/us-universe.yaml`
  (seeded by the platform's `prepare-host.sh` from [deploy/us-universe.yaml](../../deploy/us-universe.yaml),
  then operator-edited).

## Entry points

- Console script `factorlab-ingest-us`:
  - `universe --config <yaml> (--once | --daemon)`: resolve and publish the universe.
  - `daemon [--universe us_listed_equities] [--daemon] [--backfill]`: price collection.
  - `engine`: the provider-agnostic engine (validate, run, daemon, replay).
- Compose services ([deploy/compose.yaml](deploy/compose.yaml)), network `backend`, no ports:
  - `universe-us`: `universe --config /app/config/us-universe.yaml --daemon`, `mem_limit: 512m`.
  - `ingest-us`: `daemon --universe us_listed_equities --daemon`, `mem_limit: 1g`,
    `stop_grace_period: 120s`.
  Both wait on `bootstrap` (`service_completed_successfully`).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Universe file | `FACTORLAB_US_UNIVERSE_CONFIG` (compose) | `/etc/factorlab/us-universe.yaml` | mounted read-only at `/app/config/us-universe.yaml` |
| Live cadence | `US_LIVE_INTERVAL_SECONDS` | `300` | measured from the previous sweep's start |
| Schwab pacing | `SCHWAB_MIN_REQUEST_INTERVAL_SECONDS` | `1.0` | full-universe mode |
| Collector lock | `US_INGEST_LOCK` | `/app/data/us-ingest.lock` | one collector at a time (exit 2 if held) |
| ClickHouse | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USERNAME` | compose: `clickhouse`, `8123`, `default`, `factorlab` | |
| Secrets | `CLICKHOUSE_PASSWORD`, `SCHWAB_ACCESS_TOKEN` (+ `.expires_at`), `EODHD_API_KEY` | - | `us_runtime` tmpfs at `/run/secrets/app` |
| Image pin | `FACTORLAB_INGEST_US_IMAGE` | falls back to `FACTORLAB_US_IMAGE`, then `FACTORLAB_IMAGE` | bridge from the monolith |

## Data

- Writes `raw.archive` (`source` = `schwab`, `eodhd`, `github_csv`), `market.bars`,
  `ref.listings` / `ref.securities` / `ref.identifier_aliases`, `meta.expected_series`
  (`source` `schwab`: `daily` and `1min`, minute tier `us_liquid_250`),
  `meta.recovery_state`, `meta.session_coverage`, `meta.source_status`
  (`source` = `schwab` and `universe`) and `meta.ingestion_runs`.
- Pipelines (frozen strings): `us_live`, `us_recovery_daily`, `us_recovery_1min`
  (`us_recovery_{resolution}`), `us_universe_sync` (run `source` = the configured provider).
- Host path `/var/lib/factorlab/app-data` -> `/app/data` (lock file; shared with `ingest-india`).

## Dependencies and contracts

- Workspace: `factorlab-core`, `-runtime`, `-calendars`, `-ingest`, `-storage`,
  `-orchestration`, and providers `schwab`, `eodhd`, `github-csv`, `edgar`. Third-party:
  `pandas`, `pyyaml`, `pydantic`, `requests` ([pyproject.toml](pyproject.toml)).
- Contracts: the v2 write shape behind `data_contract: 1`; the universe YAML schema
  (`version: 2`, `provider`, `daily_source`, `indexes`, `providers`); the universe publish
  guard (the resolver publishes only fully validated membership). The API's US status
  marks the collector stale when `meta.source_status` is older than ten minutes.

## Observability

- Logs: `/var/log/factorlab/ingest-us/ingest-us.jsonl` and `universe-us.jsonl`:
  `uv run python tools/read_logs.py tail --component ingest-us --service universe-us`.
- Both daemons refresh `meta.source_status` about every 60 s: `schwab` reports
  `auth_required` when no valid token is present, `universe` reports `ready` / `error`;
  both write `stopped` on graceful shutdown. Query with the `factorlab-clickhouse` skill
  ([SKILL.md](../../.claude/skills/factorlab-clickhouse/SKILL.md)).
- Health kind `running` (image `HEALTHCHECK NONE`): both containers must stay running
  with no restarts through the deployer's stabilization window.

## Tests

[tests/](tests/): Schwab market client and rotating session, universe resolvers,
configured and full universe, providers, recovery.

```bash
uv run pytest components/ingest-us
```

## Release and rollback

`.\deploy\release.ps1 -Component ingest-us [-Bump ...] [-DryRun]` -> tag
`ingest-us/vX.Y.Z` -> [component-release.yml](../../.github/workflows/component-release.yml)
-> `deploy-component.sh ingest-us ...` via [_deploy.yml](../../.github/workflows/_deploy.yml),
which recreates both services. Rollback class `writer`, `data_contract: 1`: a failed
deploy restores the previous release if the contract is unchanged, otherwise stops both
services and reports `fix-forward-required` ([factorlab_deploy.py](../../deploy/host/factorlab_deploy.py)).
Bump `data_contract` when written data changes. The closure includes `configs/`. The
legacy monolith path (`release/*` tags, `deploy-release.sh`) still runs production until
the rollout completes.

## Pitfalls

- Changing only `provider` in the universe YAML switches resolvers; there is no automatic
  fallback. Restart `universe-us` after editing (`sudo factorlab-compose restart universe-us`);
  the collector picks up new membership without a restart.
- Schwab needs re-authentication in the auth Worker when its refresh token expires (7 days);
  until then the collector reports `auth_required` and collects nothing.
- Only one collector may hold `US_INGEST_LOCK`; a second `daemon` (for example a manual
  backfill in another container sharing `/app/data`) exits 2.
- `universe-us` has no `/app/data` mount; do not add state that assumes it.
- [`component-rollback.yml`](../../.github/workflows/component-rollback.yml) does not check
  `data_contract`; never roll back across a contract bump.
