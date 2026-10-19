# ingest-political

> Congressional references, the House PTR filing index and parsed PTR trades into ClickHouse v2, once a day.

## Purpose

`ingest-political` is a one-shot job: it snapshots current legislators, committees and
memberships (unitedstates.github.io `congress-legislators`), the House Clerk PTR filing
index for a year, and optionally parses the newest PTR PDFs into trades. Host cron starts
it daily at 02:15 UTC. It is a writer component (image
`ghcr.io/arjundoshi221/factorlab-ingest-political`, manifest [component.yaml](component.yaml)).

## Owns and does not own

- Owns: the `factorlab-ingest-political` CLI ([cli.py](src/factorlab/components/ingest_political/cli.py)),
  the ordered engine job in [bootstrap.py](src/factorlab/components/ingest_political/bootstrap.py),
  and the providers
  the image ships ([providers.py](src/factorlab/components/ingest_political/providers.py):
  `house_clerk`, `congress_legislators`).
- Does not own: the cron entry and wrapper (`platform`:
  [deploy/cron/factorlab-political](../../deploy/cron/factorlab-political),
  [run-political-ingest.sh](../../deploy/scripts/run-political-ingest.sh),
  [install-political-cron.sh](../../deploy/scripts/install-political-cron.sh)), the writers
  ([political sinks](../../libs/storage/src/factorlab/storage/sinks/political.py)),
  the political API routes (`api`).

## Entry points

- Console script `factorlab-ingest-political`:
  - `bootstrap [--year <YYYY>] [--ptr-limit N] [--congress 119]`: one run. `--ptr-limit 0`
    (the parser default) stores the filing index only.
  - `engine`: the provider-agnostic engine (validate, run, daemon, replay).
  - `dedupe-political-trades [--apply]`,
    `retire-duplicate-listings [--country US] [--canonical-alias schwab_symbol] [--apply]`
    and `reresolve-political-trades [--apply]`: one-off identity repairs, dry run unless
    `--apply` ([runbook](../../docs/operations/identity-repair.md)). The trade writer
    deletes versions it supersedes (the sort key includes `entity_id`).
- Compose service `ingest-political` ([deploy/compose.yaml](deploy/compose.yaml)), profile
  `jobs`, `restart: "no"`, command `bootstrap --ptr-limit ${POLITICAL_PTR_LIMIT:-20}`,
  network `backend`, no ports; waits on `bootstrap` (`service_completed_successfully`).
- Manifest mode `scheduled`, schedule `15 2 * * *` (UTC; the installer refuses a non-UTC host).
- Run now on the VPS: `sudo sh /opt/factorlab/deploy/scripts/run-political-ingest.sh`.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| PTR PDFs per run | `POLITICAL_PTR_LIMIT` (`production.env`) | `20` | newest filings by `filing_date` |
| Filing year | `--year` | current UTC year | |
| Congress | `--congress` | `119` | committee and membership scope |
| ClickHouse | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USERNAME` | compose: `clickhouse`, `8123`, `default`, `factorlab` | |
| ClickHouse password | secret `CLICKHOUSE_PASSWORD` | - | `political_runtime` tmpfs at `/run/secrets/app` |

## Data

- Writes `raw.archive` (`source` = `congress_legislators` or `house_clerk`,
  channels `<instance>:legislators|committees|memberships|filing_index|ptr`), `ref.entities`,
  `ref.identifier_aliases`, `ref.legislator_terms`, `alt.political_committees`,
  `alt.political_committee_memberships`, `alt.political_filings`, `alt.political_trades`,
  and one `meta.ingestion_runs` row per run.
- Pipeline (frozen): `political_bootstrap`, `source` = `political`, `market_code` =
  `ALT_POLITICAL`, `universe` = `house`. Requested units = 4 + PTR limit; status `success`,
  `partial` (isolated unit failures or fewer filings than the limit) or `failed` (fatal exception).
  Dataset writes retain their binding source and share the parent job run ID.
  A failed reference or index stage defers dependent stages. Negative PTR limits are rejected.

## Dependencies and contracts

- Workspace: `factorlab-core`, `-runtime`, `-calendars`, `-ingest`, `-storage`,
  `-orchestration`, `factorlab-provider-house-clerk`, `factorlab-provider-congress-legislators`;
  third-party `requests` ([pyproject.toml](pyproject.toml)). `pdfplumber` may ship only in
  this image ([tools/verify_image.py](../../tools/verify_image.py)).
- Contracts: the v2 write shape behind `data_contract: 1`; legislator matching of filings
  (canonical `legislator_name` aliases; ambiguous names stay unresolved); the political API reads these tables.

## Observability

- Logs: `/var/log/factorlab/ingest-political/ingest-political.jsonl` (the job) and
  `cron.log` (the cron wrapper's start/finish lines and compose output):
  `uv run python tools/read_logs.py tail --component ingest-political --since 26h`.
- `meta.ingestion_runs` where `pipeline = 'political_bootstrap'` is the run record; query it
  with the `factorlab-clickhouse` skill ([SKILL.md](../../.claude/skills/factorlab-clickhouse/SKILL.md)).
- Health kind `exit-zero`. There is no heartbeat.

## Tests

[tests/](tests/) (`test_political_providers.py`, `test_political_sources.py`):

```bash
uv run pytest components/ingest-political
```

## Release and rollback

`.\deploy\release.ps1 -Component ingest-political [-Bump ...] [-DryRun]` -> tag
`ingest-political/vX.Y.Z` -> [component-release.yml](../../.github/workflows/component-release.yml)
-> `deploy-component.sh ingest-political ...` via [_deploy.yml](../../.github/workflows/_deploy.yml).
Rollback class `writer`, `data_contract: 1` ([factorlab_deploy.py](../../deploy/host/factorlab_deploy.py)).
Bump `data_contract` when written data changes. The legacy monolith path (`release/*` tags,
`deploy-release.sh`) still runs production until the rollout completes.

## Pitfalls

- A deploy only swaps the fragment and image pin: `scheduled` services are neither run nor
  verified, so a broken image surfaces at the next 02:15 UTC run. Run
  `run-political-ingest.sh` by hand after a release.
- `run-political-ingest.sh` calls `docker compose --env-file production.env -f
  compose.production.yml`, not `factorlab-compose`, so it does not read the deployer's
  `state/images.env` pins; check which image the cron job actually runs.
- The subcommand `bootstrap` is unrelated to the compose service `bootstrap` (the
  `schema-migrator` readiness gate this job waits on).
- The cron entry uses `flock -n -E 75`: an overlapping run exits 75 and is skipped.
- Failed PTRs are recorded individually and the job exits nonzero on any partial run.
  Index refreshes preserve existing trade counts. `--year` filters PTR selection before limiting.
- [`component-rollback.yml`](../../.github/workflows/component-rollback.yml) does not check
  `data_contract`; never roll back across a contract bump.

**UDP service monitoring:** The manifest declares expected liveness. Set FACTORLAB_MONITOR_ADDR=monitor:9125 after the monitor rollout; empty disables sending. The process reports crashes and deliberate stops, and daemon waits report idle liveness through runtime helpers.
