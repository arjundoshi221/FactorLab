# factorlab-clickhouse

> ClickHouse connection settings, a client wrapper with an optional SSH tunnel, and the value conventions every v2 reader and writer shares.

## Purpose

The pandas-free ClickHouse access layer. Readers (the `api` component, the schema
readiness checks) use it directly; the v2 writers in `factorlab-storage` extend its
`ClickHouse` class. It sits one layer above core
(`LIBRARY_LAYERS["clickhouse"] = {"core"}` in
[test_boundaries.py](../../tests/architecture/test_boundaries.py)).

## Owns and does not own

- Owns: `ClickHouseSettings` ([settings.py](src/factorlab/clickhouse/settings.py)),
  `ClickHouse` and `open_ssh_tunnel` ([connection.py](src/factorlab/clickhouse/connection.py)),
  `rows()` ([results.py](src/factorlab/clickhouse/results.py)), `version()` and
  `decoded_text()` ([values.py](src/factorlab/clickhouse/values.py)).
- Does not own: DDL and migrations (`factorlab-schema`; note `migrate.py` builds its
  own client), ingestion-run lifecycle and table writers (`factorlab-storage`),
  query endpoints (`api` component).

## Entry points

None (library). Public API re-exported from `factorlab.clickhouse`:

- `ClickHouse.connect(settings=None, **client_options)` / `ClickHouse.from_environment()`;
  `.client` is the raw `clickhouse_connect` client; `.close()` also stops the tunnel;
  usable as a context manager.
- `open_ssh_tunnel(settings)` (needs the `ssh` extra: `paramiko`, `sshtunnel`).
- `rows(result)`: query result to dicts, naive datetimes marked UTC.
- `version(timestamp)`: monotonic nanosecond `ReplacingMergeTree` version.
- `decoded_text(value)`: FixedString bytes to `str`, NULs stripped.

## Configuration and secrets

`ClickHouseSettings` uses `env_prefix="CLICKHOUSE_"` (field names are case-insensitive).

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Host / port | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT` | `localhost`, `8123` | HTTP interface |
| User / database | `CLICKHOUSE_USERNAME`, `CLICKHOUSE_DATABASE` | `factorlab`, `factorlab` | |
| Password | secret `CLICKHOUSE_PASSWORD` | `factorlab_dev` | Via `get_secret`: `CLICKHOUSE_PASSWORD_FILE`, `FACTORLAB_SECRETS_DIR/CLICKHOUSE_PASSWORD`, then env |
| SSH tunnel host | `CLICKHOUSE_SSH_HOST` | unset (no tunnel) | Set to tunnel to a loopback-bound server (the VPS) |
| SSH user / port | `CLICKHOUSE_SSH_USER`, `CLICKHOUSE_SSH_PORT` | `ubuntu`, `22` | |
| SSH key | `CLICKHOUSE_SSH_KEY_PATH` | unset | Takes precedence over the SSH password |
| SSH password | secret `CLICKHOUSE_SSH_PASSWORD` | unset | Falls back to ssh-agent / default identities |

Secret values live in the Cloudflare secret stores and the runtime secret volumes.

## Data

No tables of its own. `version()` defines the `version UInt64` column convention for
every ReplacingMergeTree row written by storage (latest version wins at `FINAL`).
Schema reference: [06-schema-rehau.md](../../docs/architecture/06-schema-rehau.md).

## Dependencies and contracts

- Workspace: `factorlab-core` (settings, secrets). Third party:
  `clickhouse-connect>=0.8`; optional `ssh` extra `paramiko>=3.4`, `sshtunnel>=0.4`.
- Must stay free of pandas and of `factorlab-storage` so the `api` and
  `schema-migrator` images stay light.
- `version()` must stay strictly increasing within a process and at least
  `time.time_ns()`, because migration and resolver rows use nanosecond versions.

## Observability

Nothing is logged here; `clickhouse_connect` is set to WARNING by
`factorlab.core.logging.configure_logging`. To inspect production data use the
[factorlab-clickhouse](../../.claude/skills/factorlab-clickhouse/SKILL.md) skill
(read-only, bounded, through its own tunnel), only on the user's request.

## Tests

`uv run pytest libs/clickhouse` (settings sources and precedence, secret masking,
bare-name protection, immutability, connect/tunnel wiring with a stubbed client).
No test contacts a server.

## Release and rollback

Never released alone. Components whose closure includes it
([affected.py](../../tools/affected.py)): `api`, `schema-migrator`, `ingest-broker`,
`ingest-india`, `ingest-political`, `ingest-us`. Roll back by redeploying their
previous tags; the library version is not tagged.

## Pitfalls

- A missing `CLICKHOUSE_PASSWORD` falls back to the dev default `factorlab_dev`, so a
  broken secret volume shows up as an authentication error, not a config error.
- `factorlab.schema.migrate.create_client` does not use `ClickHouseSettings`: it reads
  `CLICKHOUSE_USER`/`CLICKHOUSE_USERNAME` (default `default`) and `CLICKHOUSE_SECURE`.
- Never compute versions with `time.time()` or a source timestamp; always `version()`.
- `open_ssh_tunnel` aliases `paramiko.DSSKey` to `RSAKey` when paramiko lacks it,
  because `sshtunnel` still references it; do not remove the shim casually.
