# factorlab-schema

> The ClickHouse v2 schema as checksum-tracked SQL waves, the forward-only migration runner, startup readiness gates, post-release write verification and the DDL generator.

## Purpose

Owns the shape of the v2 database. The SQL waves ship as package data and are
applied by a reviewed, operator-run migration; the `schema-migrator` component's
`bootstrap` service only checks readiness and applies no DDL. Layer:
`LIBRARY_LAYERS["schema"] = {"core", "clickhouse"}`
([test_boundaries.py](../../tests/architecture/test_boundaries.py)). Design source:
[06-schema-rehau.md](../../docs/architecture/06-schema-rehau.md).

## Owns and does not own

- Owns: the waves in [sql/clickhouse/v2/](src/factorlab/schema/sql/clickhouse/v2/README.md)
  with [checksums.lock](src/factorlab/schema/sql/clickhouse/v2/checksums.lock) and
  [deferred.json](src/factorlab/schema/sql/clickhouse/v2/deferred.json);
  [migrate.py](src/factorlab/schema/migrate.py) (plan/status/apply/validate, journal,
  lock); [readiness.py](src/factorlab/schema/readiness.py);
  [storage_policy.py](src/factorlab/schema/storage_policy.py);
  [verify_writes.py](src/factorlab/schema/verify_writes.py);
  [codegen.py](src/factorlab/schema/codegen.py); `v2_sql_dir()` in
  [resources.py](src/factorlab/schema/resources.py).
- Does not own: writing data rows (`factorlab-storage`), the connection class
  (`factorlab-clickhouse`), the checksum lock tooling
  ([schema_checksums.py](../../tools/schema_checksums.py)), the CLI wrapper
  (`factorlab-db` in `components/schema-migrator`).

## Entry points

- `factorlab-db bootstrap` -> `readiness.main()` then `storage_policy.main()`.
- `factorlab-db verify-writes --since <tz-aware ISO> [--wait-seconds N] [--require-universe-ready]`.
- `factorlab-db migrate ...` = `migrate.main`, also
  [migrate_clickhouse_v2.py](../../scripts/migrate_clickhouse_v2.py):
  `plan [--phase] [--through-wave]`, `status`,
  `apply --phase schema|backfill|rbac --through-wave N [--yes] [--dry-run] [--lock-timeout 3600]`,
  `validate --through-wave N`; global `--source-database`.
- `codegen.main` via [generate_clickhouse_v2_schema.py](../../scripts/generate_clickhouse_v2_schema.py).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Readiness, policy, verify-writes | `CLICKHOUSE_*`, secret `CLICKHOUSE_PASSWORD` | see `factorlab-clickhouse` | `ClickHouse.from_environment()` |
| Migrate host / port | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT` | `localhost`, `8123` (`8443` when secure) | Own client in `create_client` |
| Migrate user | `CLICKHOUSE_USER`, else `CLICKHOUSE_USERNAME` | `default` | |
| Migrate password | `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_PASSWORD_FILE`, `FACTORLAB_SECRETS_DIR/CLICKHOUSE_PASSWORD` | empty | Env first, then files |
| TLS / timeout | `CLICKHOUSE_SECURE`, `CLICKHOUSE_CONNECT_TIMEOUT` | `false`, `10` | |
| Insert partitions | `CLICKHOUSE_MIGRATION_MAX_PARTITIONS_PER_INSERT_BLOCK` | unset | 100-10000, migration client only |
| Legacy source DB | `FACTORLAB_LEGACY_DATABASE` / `--source-database` | `factorlab` | Must match `[A-Za-z_][A-Za-z0-9_]*` |
| Integration tests | `CLICKHOUSE_V2_INTEGRATION=1` | off | Disposable server, port default `18123` |

`migrate.py` reads the environment directly (11 reads ratcheted in
[env_access_allowlist.txt](../../tests/architecture/env_access_allowlist.txt), N4).

## Data

- Databases: `ref`, `market`, `fundamentals`, `alt`, `book`, `risk`, `derived`,
  `broker`, `meta`, `raw`, `research`.
- Migration bookkeeping: `meta.schema_migrations` (journal, latest `version` wins),
  `meta.migration_runs`, lock table `default._factorlab_v2_migration_lock`.
- Readiness requires the `REQUIRED_TABLES` set, `wave_09_schema_application_cutover`
  `succeeded`, and `listing_id` keys on `meta.expected_series`,
  `meta.session_coverage`, `meta.recovery_state`; `raw.archive` must use storage
  policy `raw_archive`; server ClickHouse 25.3+.
- `verify-writes` counts fresh `market.bars`, `market.futures_contract_bars`,
  `alt.political_trades` rows with real `raw_id`/`ingest_run_id`; universe readiness
  needs `meta.source_status` `source = 'universe'` `ready` and at least 450 active
  `meta.expected_series` rows for `source = 'schwab'`, `resolution = 'daily'`.

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-clickhouse`. Third party: `clickhouse-connect>=0.8`.
- File names match `wave_NN_<schema|views|backfill|rbac|validate>[_suffix].sql`;
  `views` run in the `schema` phase; order is phase, wave, file name.
- Checksum = SHA-256 of the SQL with CRLF normalized; drift against the journal aborts.
- SQL placeholders: `{{source_database}}`, `{{migration_run_id}}`.
- The `api` component reads the wave files through `v2_sql_dir()` (schema map).

## Observability

`bootstrap` logs as component `schema-migrator`, service `bootstrap`; `verify-writes`
as service `verify-writes` (JSON via `factorlab.core.logging`). The migration CLI
prints to stdout and exits 2 on `MigrationError`. Check applied state with
[factorlab-clickhouse](../../.claude/skills/factorlab-clickhouse/SKILL.md) and
bootstrap failures with [factorlab-logs](../../.claude/skills/factorlab-logs/SKILL.md).

## Tests

`uv run pytest libs/schema` (split/ordering/checksums, dry run never connects,
`--yes` required, resume, lock, readiness, generated SQL equals the design doc,
forward tables). Server tests skip unless `CLICKHOUSE_V2_INTEGRATION=1`. The lock is
checked by `uv run pytest tests/test_schema_checksums.py`.

## Release and rollback

Never released alone. Components whose closure includes it
([affected.py](../../tools/affected.py)): `schema-migrator` (`rollback: forward-only`)
and `api` (`rollback: auto`). Applying waves to production is a separate, reviewed,
user-requested operation; a release never runs DDL.

## Pitfalls

- An `applied` migration in `checksums.lock` can never change: fix forward with a new
  wave (`uv run python tools/schema_checksums.py add`). Never hand-edit v2 DDL; change
  doc 06 and run the generator, which needs a checkout (it reads `docs/` under
  `FACTORLAB_HOME`).
- New tables for an applied namespace go in `FORWARD_TABLES` in `codegen.py` (the
  SQL README still names the old script path).
- `--through-wave` accepts every wave that has a file on disk, so a new wave is
  selectable as soon as it lands. Record it in `checksums.lock` first
  (`tools/schema_checksums.py add`), and mark it `applied` after the production run.
- A succeeded `backfill` migration is re-run on each apply (catch-up); schema and rbac
  are skipped once succeeded.
