# factorlab-orchestration

> The provider-agnostic engine CLI (`validate`, `run`, `daemon`, `replay`, `sync-priorities`) that runs bindings through `factorlab-ingest` into `factorlab-storage` sinks.

## Purpose

The top library layer: it turns `configs/ingestion/bindings.yaml` into runs. Each
ingest component exposes it as its `engine` subcommand with the providers it ships,
and [factlab_ingest.py](../../scripts/factlab_ingest.py) runs it with every provider
for development and ops. It never imports a provider (doc 07 rule R4; test rule R4 in
[test_boundaries.py](../../tests/architecture/test_boundaries.py)).

## Owns and does not own

- Owns: [cli.py](src/factorlab/orchestration/cli.py): argument parsing, binding
  selection, request building per dataset, sink choice (dry run or ClickHouse), exit
  status, the daemon loop and its session gate (`MARKET_WINDOWS`, `in_session`).
- Does not own: the engine, registry and bindings model (`factorlab-ingest`), sinks
  (`factorlab-storage`), provider lists (`PROVIDERS` in each ingest component's `providers.py`), compose
  services and schedules (`components/*/component.yaml`), the legacy production
  daemons (each component's `legacy/` package).

## Entry points

`main(argv, *, providers, prog=None)`; reached as `factorlab-ingest-<x> engine ...`
(for example `factorlab-ingest-us engine validate`) or
`python scripts/factlab_ingest.py ...`. Subcommands:

- `validate`: load bindings for the shipped providers and check them against the registry.
- `run --dataset D --market IND|USA [--resolution R] [--instance I] [--dry-run]` with
  `--listing`, `--universe`, `--exchange`, `--instrument alias[,symbol,isin]`,
  `--symbol`, `--lookback-minutes` (default 1440), `--recent-filings` (20),
  `--chamber` (`house`), `--cik cik[:ticker]`.
- `daemon`: `run` every `--interval-seconds` (60) until SIGTERM, optionally
  `--sessions-only` with `--pre-open-minutes` (0) and `--post-close-minutes` (5).
- `replay --raw-id <uuid>...`: re-normalize archived captures (always writes).
- `sync-priorities [--dry-run]`: write binding priorities to `ref.source_priorities`.

Global flags: `--bindings PATH`, `-v/--verbose` (DEBUG logging).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Bindings | `--bindings`, else `FACTORLAB_CONFIG_DIR`/`FACTORLAB_HOME` | `configs/ingestion/bindings.yaml` | Loaded by `factorlab-ingest` |
| ClickHouse | `CLICKHOUSE_*`, secret `CLICKHOUSE_PASSWORD` | see `factorlab-clickhouse` | Only when not `--dry-run` |
| Logging | `FACTORLAB_COMPONENT`, `FACTORLAB_LOG_*` | see `factorlab-core` | `configure_logging` at start |

No direct environment reads. Provider credentials are resolved inside providers.

## Data

- Writes through `ClickHouseSinks.from_environment(market)`: `IND` uses the India
  storage, `USA` the US/broker storage. Tables are whatever the dataset's sink writes,
  plus `raw.archive` and `meta.ingestion_runs`.
- `--dry-run` swaps in `factorlab.ingest.memory.InMemorySink` (no ClickHouse).
- `sync-priorities` writes `ref.source_priorities` (wave 10, `pending` in
  `checksums.lock`); country codes map `IND` -> `IN`, `USA` -> `US`.
- Session gate: `IND` = `XBOM` 09:15-15:30 Asia/Kolkata; `USA` = `XNYS` 09:30-16:00
  America/New_York.

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-runtime`, `factorlab-calendars`,
  `factorlab-ingest`, `factorlab-storage`; third party `tzdata`.
- Exit status (`ExitCode`): `0` when every run is `success`, `3` when every run
  `failed`, otherwise `2`. `run` prints one JSON summary per binding
  (`run_id`, `source`, `pipeline`, `status`, `rows_written`, `failed_units`).
- Only bindings whose provider is in `providers` are loaded (`for_providers`).

## Observability

Logger `factlab_ingest`; lines inside a run carry `run_id`, `pipeline` and `source`
(bound by `ingestion_run`). The daemon ticks heartbeat
`ingest-<dataset>-<market>` with dots replaced by dashes (e.g.
`ingest-market-bars-IND`) after each cycle, and logs "ingestion cycle failed;
retrying next interval" on an exception. Use the
[factorlab-logs](../../.claude/skills/factorlab-logs/SKILL.md) `run --run-id` view and
[factorlab-clickhouse](../../.claude/skills/factorlab-clickhouse/SKILL.md) for
`meta.ingestion_runs`.

## Tests

This member has no `tests/` directory; `uv run pytest libs/orchestration` collects
nothing. Its tests live at the root: `uv run pytest tests/test_factlab_ingest_cli.py`
(repository bindings validate, dry-run bars via registry and memory sink, session
gate, daemon loop). Boundaries: `uv run pytest tests/architecture`.

## Release and rollback

Never released alone. Components whose closure includes it
([affected.py](../../tools/affected.py)): `ingest-broker`, `ingest-india`,
`ingest-political`, `ingest-us`. Roll back by redeploying their previous tags
(`rollback: writer`); the library version is not tagged.

## Pitfalls

- The compose services in the component manifests run the legacy daemons, not
  `engine daemon`; all bindings are `shadow` until each provider's cutover (doc 07 §15.2).
- `replay` has no `--dry-run` and always opens ClickHouse.
- `sync-priorities` without `--dry-run` fails until wave 10 is applied.
- `--market` accepts only `IND` and `USA`; a new market needs `_COUNTRY`,
  `MARKET_WINDOWS` and a storage class in `ClickHouseSinks.from_environment`.
- `LIBRARY_LAYERS` also allows `clickhouse` and `schema` imports here, but the
  pyproject does not declare them directly; add the dependency if you import them.
