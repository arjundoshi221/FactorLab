# factorlab-core

> Bottom layer of every FactorLab image: secret lookup, typed settings, on-disk paths, JSON logging, build stamp and the container health probe.

## Purpose

`factorlab.core` is the library every other workspace member builds on. It has no
workspace dependencies (layer `core` in `LIBRARY_LAYERS`,
[test_boundaries.py](../../tests/architecture/test_boundaries.py)) and only needs
`pydantic` / `pydantic-settings`. It is also the only package allowed to read the
environment directly (rule N4, [test_package_hygiene.py](../../tests/architecture/test_package_hygiene.py)).

## Owns and does not own

- Owns: secret resolution ([secrets.py](src/factorlab/core/secrets.py)), the
  `FactorLabSettings` base class and the `Secret("NAME")` marker
  ([settings.py](src/factorlab/core/settings.py)), every on-disk location
  ([paths.py](src/factorlab/core/paths.py)), structured logging
  ([logging.py](src/factorlab/core/logging.py)), the image build stamp
  ([build.py](src/factorlab/core/build.py)) and `factorlab-healthcheck`
  ([healthcheck.py](src/factorlab/core/healthcheck.py)).
- Does not own: ClickHouse settings (`factorlab-clickhouse`), heartbeat writers,
  locks and exit codes (`factorlab-runtime`), rendering secrets into the volume
  (the `secrets-agent` component), any provider or component settings model (each
  member subclasses `FactorLabSettings` itself).

## Entry points

- Console script `factorlab-healthcheck` (stdlib only, used by Docker `HEALTHCHECK`):
  `heartbeat <service> [--max-age 300]`, `http <url> [--timeout 5]`,
  `files <path>... [--max-age N]`. Exit 0 healthy, 1 unhealthy.
- `get_secret(name, default=None)`, `require_secret(name)` (raises `OSError`).
- `FactorLabSettings`, `Secret`: subclass with `env_prefix`; settings are frozen,
  ignore unknown and empty variables, and never read `.env`.
- `configure_logging(...)`, `log_context(**fields)`, `bind(...)`, `unbind(...)`, `redact(text)`.
- `paths.home()`, `config_dir()`, `raw_dir(source)`, `state_dir(source)`,
  `token_path(source)`, `log_dir()`, `heartbeat_path(service)`, `assert_under_root(path, root)`.
- `build_info()` -> `BuildInfo(component, version, commit, release_id)`.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Secret volume | `FACTORLAB_SECRETS_DIR` | unset | Production compose sets `/run/secrets/app`; file `<dir>/<NAME>` |
| Per-secret file | `<NAME>_FILE` | unset | Checked before the volume, then the `<NAME>` env var |
| Home | `FACTORLAB_HOME` | enclosing checkout (has `configs/ingestion/bindings.yaml`), else cwd | Images set `/app` |
| Config dir | `FACTORLAB_CONFIG_DIR` | `<home>/configs` | |
| Raw / state / token roots | `FACTORLAB_RAW_ROOT`, `FACTORLAB_STATE_ROOT`, `FACTORLAB_TOKEN_ROOT` | `<home>/data`, `<home>/data/_state`, `<home>/data` | Legacy per-source layout maps in `paths.py` |
| Log root | `FACTORLAB_LOG_ROOT` | `<home>/logs` | `paths.log_dir()`; used by the notify JSONL audit file |
| Heartbeat root | `FACTORLAB_HEARTBEAT_ROOT` | `<home>/data/_heartbeat` | One file per service, liveness by mtime |
| Log file sink | `FACTORLAB_LOG_DIR` | unset (stderr only) | Compose sets `/var/log/factorlab/<component>` |
| Log fields | `FACTORLAB_COMPONENT`, `FACTORLAB_SERVICE`, `FACTORLAB_LOG_LEVEL`, `FACTORLAB_LOG_FORMAT` | `factorlab`, = component, `INFO`, `json` (`text` on a TTY) | |
| Build stamp | `FACTORLAB_VERSION`, `FACTORLAB_COMMIT`, `FACTORLAB_RELEASE_ID` | unset | Set by image builds; release id is the legacy monolith's |

Core itself reads no secret; it resolves whatever names callers pass. Secret
values live in the Cloudflare secret stores and the runtime secret volumes.

## Data

No ClickHouse access. Files: `<FACTORLAB_LOG_DIR>/<service>.jsonl` (created 0640,
reopened after logrotate via `WatchedFileHandler`), heartbeat files under
`FACTORLAB_HEARTBEAT_ROOT`, and the raw/state/token directories returned by `paths`.
Every path getter runs `assert_under_root` and raises `PermissionError` on escape.

## Dependencies and contracts

- Third party: `pydantic>=2.5`, `pydantic-settings>=2.1`; `healthcheck` and
  `logging` are stdlib only.
- Settings precedence is fixed: keyword args, then `Secret(...)` sources, then env,
  then defaults. Never enable `populate_by_name` (a stray `HOST`/`USERNAME` would
  configure fields; see the test in `libs/clickhouse/tests/test_settings.py`).
- Log record fields: `ts`, `level`, `component`, `service`, `version`, `commit`,
  `logger`, `msg`, `exc`, plus `extra=` and context fields. Downstream log readers
  (`tools/read_logs.py`) depend on these names.

## Observability

This member defines the logging contract: one JSON object per line on stderr and
optionally in the file sink. Keys matching password/secret/token/api key/
authorization/cookie are replaced with `[redacted]`, and credential-looking text in
messages is redacted. `factorlab.ingest.provider.ingestion_run` binds `run_id`,
`pipeline` and `source` through `log_context`, so every line of a run can be pulled
with the [factorlab-logs](../../.claude/skills/factorlab-logs/SKILL.md) skill and
cross-checked against `meta.ingestion_runs` with
[factorlab-clickhouse](../../.claude/skills/factorlab-clickhouse/SKILL.md).
Uncaught exceptions (main thread and threads) are logged by `factorlab.uncaught`.

## Tests

`uv run pytest libs/core` (paths, logging, secrets, build, healthcheck).
Settings behaviour is covered in `uv run pytest libs/clickhouse`; the N1–N5 rules in
`uv run pytest tests/architecture`.

## Release and rollback

Never released alone. It is in the closure of every Python component
([affected.py](../../tools/affected.py)): `api`, `ingest-broker`, `ingest-india`,
`ingest-political`, `ingest-us`, `schema-migrator` and `secrets-agent`. A change here
makes all seven release units affected; roll back by redeploying each component's
previous tag (rollback class per `components/*/component.yaml`: `auto`, `writer` or
`forward-only`). The `0.1.0` version in `pyproject.toml` is not tagged.

## Pitfalls

- `paths` constants (`HOME`, `RAW_ROOT`, `HEARTBEAT_ROOT`, ...) are computed at
  import. Set env vars before importing; tests monkeypatch the module attributes.
- `FACTORLAB_LOG_DIR` (log file sink) and `FACTORLAB_LOG_ROOT` (`paths.log_dir()`)
  are different knobs.
- Rotating tokens (Upstox daily, Schwab) must not be frozen in a settings object:
  store the secret name and call `get_secret` per request.
- The `secrets.py` docstring still mentions Vault Agent; secrets are now rendered by
  the `secrets-agent` component.
- Adding an `__init__.py` under `src/factorlab/` breaks the PEP 420 namespace (N1).
- New env reads anywhere else must go through a `FactorLabSettings` subclass (N4).
