# factorlab-ingest

> Provider-agnostic ingestion SDK: the provider contract, dataset catalogue, error taxonomy, registry, bindings and the engine that runs a source into a sink.

## Purpose

The contract layer between providers (`providers/*`, packages `factorlab.sources.*`)
and the DB service (`factorlab-storage`). Providers implement datasets defined here;
sinks implement the protocols defined here; the engine joins them without knowing
either side. Design: [07-ingestion-provider-abstraction.md](../../docs/architecture/07-ingestion-provider-abstraction.md).
Layer: `LIBRARY_LAYERS["ingest"] = {"core", "calendars"}`
([test_boundaries.py](../../tests/architecture/test_boundaries.py)); today it imports only core.

## Owns and does not own

- Owns: `RawCapture`, `Provenance`, `RunContext`, `ingestion_run`, `run_provider`,
  `NullProviderStorage` ([provider.py](src/factorlab/ingest/provider.py)); the
  `DATASETS` catalogue, record/request types and sink protocols
  ([datasets/](src/factorlab/ingest/datasets/__init__.py)); the read ports
  ([ports.py](src/factorlab/ingest/datasets/ports.py)); errors
  ([errors.py](src/factorlab/ingest/errors.py)) and HTTP mapping
  ([transport.py](src/factorlab/ingest/transport.py)); the registry
  ([registry.py](src/factorlab/ingest/registry.py)); bindings and provider settings
  ([bindings.py](src/factorlab/ingest/bindings.py)); the engine
  ([engine.py](src/factorlab/ingest/engine.py)); `InMemorySink`
  ([memory.py](src/factorlab/ingest/memory.py)); `SlidingWindowLimiter`,
  `diff_snapshot`, ISIN/CUSIP and legislator name keys, URL/error redaction.
- Does not own: concrete providers (`providers/*`), ClickHouse sinks and UUID minting
  (`factorlab-storage`, rule R5 of doc 07), the CLI and daemon loop
  (`factorlab-orchestration`), which providers a component ships (its `providers.py`).

## Entry points

None (library). Main calls: `register_source(cls)` (from a provider's `__init__`),
`load_providers(names)`, `source_for(binding)`, `load_bindings(path=None)`,
`validate_bindings(bindings, REGISTRY)`, `run_binding(binding, adapter, sink, request)`,
`replay(...)`, `bar_request(...)`, `reference_request(...)`, `source_priority_rows(bindings)`.

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Bindings file | via `FACTORLAB_CONFIG_DIR` / `FACTORLAB_HOME` | `<config_dir>/ingestion/bindings.yaml` | [bindings.yaml](../../configs/ingestion/bindings.yaml) |
| Provider settings | same | `<config_dir>/sources/<provider>.yaml` | `instances.<name>` blocks deep-merge over the base |

No environment reads and no secrets here. Provider YAML holds secret *names* only
(e.g. `token_env`); providers resolve them with `factorlab.core.secrets.get_secret`.

## Data

Defines, but does not write, the dataset ids and their tables: `ref.listings`,
`ref.contracts`, `ref.universe_membership`, `ref.legislators`,
`alt.political_filings`, `alt.political_trades`, `fundamentals.filings`,
`broker.snapshot`, `market.bars`, `market.futures_contract_bars` (see
`DatasetSpec.tables`). Frozen strings it produces:

- `Binding.source_name`: the provider name (`upstox`, `schwab`, ...), or
  `<provider>:shadow` for a `shadow` binding.
- `Binding.pipeline`: `<dataset>[.<resolution>]:<instance>`, e.g. `market.bars.1min:upstox`.
- `FetchUnit.source_channel` must equal the instance name or start with `<instance>:`.
- Run statuses `success`, `partial`, `failed`, `cancelled`; reference modes
  `authoritative`, `alias_only`, `resolve_only`; roles `primary`, `secondary`,
  `shadow`, `disabled`.

## Dependencies and contracts

- Workspace: `factorlab-core`. Third party: `pydantic>=2.5`, `pyyaml>=6.0`.
- `DatasetSource`: `plan` pure, `fetch` impure returning one `RawCapture` (body bytes,
  tz-aware `fetched_at`), `normalize` pure and offline (doc 07 R7).
- Sinks: one write method per dataset (`DatasetSpec.sink_method`) taking
  `rows, *, provenance` and returning `WriteResult`; writes need an open run whose id
  equals `provenance.ingest_run_id`.
- Engine policy: `RateLimited` sleeps `retry_after` (capped at 60 s) and retries once;
  `TransientError` retries twice with backoff; `AuthRequired`/`QuotaExhausted` fail
  the rest of the instance's units; other errors fail only that unit.
- Bindings load checks: known dataset, resolution present iff required, unique
  instance per key, one `primary` per key, distinct priorities for readable
  source-keyed bindings. `for_providers` narrows to what a component ships.

## Observability

`ingestion_run` wraps a run in `log_context(run_id=..., pipeline=..., source=...)`, so
every line carries the `meta.ingestion_runs.run_id`; failed units log a warning with a
redacted error. Follow a run with the
[factorlab-logs](../../.claude/skills/factorlab-logs/SKILL.md) skill and its row with
[factorlab-clickhouse](../../.claude/skills/factorlab-clickhouse/SKILL.md).

## Tests

`uv run pytest libs/ingest` (provider contract, datasets, bindings, engine policy,
security, universe diff). Providers run the source conformance suite from
`factorlab-testkit`; boundaries in `uv run pytest tests/architecture`.

## Release and rollback

Never released alone. Components whose closure includes it
([affected.py](../../tools/affected.py)): `ingest-broker`, `ingest-india`,
`ingest-political`, `ingest-us` (all `rollback: writer`). Roll back by redeploying
their previous tags; the library version is not tagged.

## Pitfalls

- Every binding in `configs/ingestion/bindings.yaml` is `role: shadow` today;
  production rows still come from each component's legacy daemon.
- A shadow of a dataset that is not `source_keyed` (everything except the two bar
  datasets) fetches, archives and normalizes but writes nothing (`counts_only`).
- `CONFIGS_DIR` / `BINDINGS_PATH` are resolved at import; set env vars first.
- Adding a dataset means a `DatasetSpec` here plus a sink in storage; the engine
  itself never needs a provider import (R4; `test_new_ingestion_layers_are_clean`).
