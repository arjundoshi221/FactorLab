# schema-migrator

> One-shot ClickHouse v2 readiness gate (`bootstrap`), write verification, and the reviewed forward-only migration runner.

## Purpose

`schema-migrator` ships `factorlab-db`. Its compose service `bootstrap` is the gate every
writer and the API wait on: it fails unless the v2 schema is complete and `raw.archive`
uses its storage policy. It applies no DDL. The same image carries `verify-writes` and the
forward-only wave runner `migrate`, which is run only as a separate, reviewed operation.
Image `ghcr.io/arjundoshi221/factorlab-schema-migrator`, manifest [component.yaml](component.yaml).

## Owns and does not own

- Owns: the `factorlab-db` CLI ([cli.py](src/factorlab/components/schema_migrator/cli.py)),
  the `bootstrap` compose service and its place as the writers' `depends_on` gate.
- Does not own: the checks and the runner themselves, which live in `libs/schema`
  ([readiness.py](../../libs/schema/src/factorlab/schema/readiness.py),
  [storage_policy.py](../../libs/schema/src/factorlab/schema/storage_policy.py),
  [verify_writes.py](../../libs/schema/src/factorlab/schema/verify_writes.py),
  [migrate.py](../../libs/schema/src/factorlab/schema/migrate.py)) with the wave SQL under
  `libs/schema/src/factorlab/schema/sql/`; ClickHouse itself and its storage config
  (`platform`, [deploy/clickhouse/](../../deploy/clickhouse/)).

## Entry points

- Console script `factorlab-db`:
  - `bootstrap` (no arguments): `readiness.main()` then `storage_policy.main()`.
  - `verify-writes ...`: wait for a fresh raw-to-curated v2 write after a release.
  - `migrate plan|status|apply|validate ...`: the wave runner. `plan` needs no connection;
    `apply --phase schema|backfill|rbac --through-wave N` mutates only with `--yes`
    (`--dry-run` prints the selection); it takes the lock table
    `default._factorlab_v2_migration_lock`.
- Compose service `bootstrap` ([deploy/compose.yaml](deploy/compose.yaml)):
  `restart: "no"`, network `backend`, waits for `clickhouse` `service_healthy`. Manifest
  mode `run-on-deploy`: the deployer runs `compose run --rm --no-deps bootstrap`.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| ClickHouse | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USERNAME` | compose: `clickhouse`, `8123`, `default`, `factorlab` | `migrate` also reads `CLICKHOUSE_USER`, `CLICKHOUSE_SECURE`, `CLICKHOUSE_CONNECT_TIMEOUT` |
| ClickHouse password | secret `CLICKHOUSE_PASSWORD` (or `CLICKHOUSE_PASSWORD_FILE`) | - | `political_runtime` tmpfs at `/run/secrets/app` |
| Migration tuning | `CLICKHOUSE_MIGRATION_MAX_PARTITIONS_PER_INSERT_BLOCK`, `FACTORLAB_LEGACY_DATABASE` | -, `factorlab` | `migrate` only |

## Data

- `bootstrap` reads `system.tables` and `meta.schema_migrations`. It requires the tables in
  `REQUIRED_TABLES` (`raw.archive`, `ref.*`, `market.bars`, `market.futures_contract_bars`,
  `alt.political_*`, `meta.ingestion_runs`, `meta.expected_series`, `meta.session_coverage`,
  `meta.recovery_state`, `meta.source_status`, `meta.hub_schema_layouts`), migration
  `wave_09_schema_application_cutover` with status `succeeded`, `listing_id` sort keys on
  the three `meta` state tables, and `raw.archive` on storage policy `raw_archive`.
- `migrate` writes `meta.schema_migrations` and `meta.migration_runs` and applies wave files
  named `wave_NN_<phase>[_name].sql`.
- `verify-writes` reads `meta.ingestion_runs`, `meta.source_status`, `meta.expected_series`.

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-runtime`, `factorlab-schema` ([pyproject.toml](pyproject.toml)).
- Contract: every writer and `api` declare `depends_on: bootstrap:
  service_completed_successfully`; a failing gate keeps them from starting on a fresh `up`.
- Schema checksums: [tools/schema_checksums.py](../../tools/schema_checksums.py) and
  [tests/test_schema_checksums.py](../../tests/test_schema_checksums.py).

## Observability

- Logs: `/var/log/factorlab/schema-migrator/bootstrap.jsonl` (`service=bootstrap`), e.g.
  `ClickHouse v2 schema is ready.` / `raw.archive uses the raw_archive storage policy.`:
  `uv run python tools/read_logs.py tail --component schema-migrator`.
- Migration state: `meta.schema_migrations` and `meta.migration_runs` via the
  `factorlab-clickhouse` skill ([SKILL.md](../../.claude/skills/factorlab-clickhouse/SKILL.md)).
- Health kind `exit-zero`. Validation record of the v2 cutover:
  [clickhouse-v2-data-completion.md](../../docs/operations/clickhouse-v2-data-completion.md).

## Tests

[tests/test_cli.py](tests/test_cli.py) (command wiring); the checks are tested in `libs/schema`:

```bash
uv run pytest components/schema-migrator libs/schema
```

## Release and rollback

`.\deploy\release.ps1 -Component schema-migrator [-Bump ...] [-DryRun]` -> tag
`schema-migrator/vX.Y.Z` -> [component-release.yml](../../.github/workflows/component-release.yml)
-> `deploy-component.sh schema-migrator ...` via [_deploy.yml](../../.github/workflows/_deploy.yml),
which runs `bootstrap` once. Rollback class `forward-only`: on failure the deployer reports
`failed-forward-only` and changes nothing else ([factorlab_deploy.py](../../deploy/host/factorlab_deploy.py));
fix forward with a new release. The legacy monolith path (`release/*`, `deploy-release.sh`)
still runs production until the rollout completes.

## Pitfalls

- A release never migrates. Production waves (`factorlab-db migrate apply --yes`) are a
  separate, reviewed, explicitly requested operation; never add DDL to `bootstrap`.
- Writers are recreated with `--no-deps`, so a component deploy does not re-run the gate;
  it runs again on a compose `up` that starts dependents (for example the legacy
  `deploy-release.sh`), where a failure blocks every writer and `api`.
- A failed deploy leaves the new pin and fragment in place (nothing is rolled back).
- The compose service name `bootstrap` is not the `ingest-political bootstrap` subcommand.
