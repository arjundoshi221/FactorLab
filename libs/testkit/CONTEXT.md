# factorlab-testkit

> Dev-only test support: the provider source-conformance suite and its fixture helpers, a stateful fake ClickHouse for sink tests, and IBKR fakes.

## Purpose

Shared pytest helpers that providers, libraries and components import from their own
`tests/`. It is installed through the workspace `dev` dependency group, never into a
component image (images build with `uv sync --frozen --no-dev`). It is exempt from
the library layer order (R7 in
[test_boundaries.py](../../tests/architecture/test_boundaries.py)).

## Owns and does not own

- Owns: fixture loading, request parsing and `regen`
  ([conformance.py](src/factorlab/testkit/conformance.py)); the suite factory
  `conformance_tests(provider)` ([source_conformance.py](src/factorlab/testkit/source_conformance.py));
  `FakeClickHouse` ([fake_clickhouse.py](src/factorlab/testkit/fake_clickhouse.py));
  `make_*` builders, `mock_ib_paper`/`mock_ib_live` and `wave7_columns`
  ([ibkr.py](src/factorlab/testkit/ibkr.py)).
- Does not own: the fixtures themselves (each provider's
  `providers/<name>/tests/fixtures/<dataset>/`), the sink conformance scenarios
  (`libs/storage/tests/test_sink_conformance.py`), the source and sink contracts
  (`factorlab-ingest`), the SQL it reads (`factorlab-schema`).

## Entry points

- `globals().update(conformance_tests("<provider>"))` in a provider's
  `tests/test_conformance.py`: generates `test_every_source_has_fixtures`,
  `test_normalize_matches_fixtures_offline_and_deterministically`,
  `test_plan_honours_capabilities` for every registered `(provider, dataset)`.
- `uv run python -m factorlab.testkit.conformance regen <provider>`: rewrite
  `*.expected.json` after an intentional normalization change (review the diff).
- `FakeClickHouse()` passed where a `clickhouse_connect` client is expected.
- IBKR fixtures are re-exported by the `conftest.py` of test directories that need them
  (`providers/ibkr/tests`, `components/ingest-broker/tests`).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| (none) | - | - | No environment reads, no secrets, no network |

The conformance tests block the network: `socket.socket.connect`,
`socket.create_connection` and `requests.Session.request` are patched to refuse.

## Data

- Fixture layout per provider and dataset: `<case>.capture.json` (serialized
  `RawCapture`), `<case>.expected.json` (records `normalize` must return),
  `requests.json` (for `plan` checks).
- `FakeClickHouse` keeps the latest version per key (`FINAL` semantics) for the tables
  in its `KEYS` map (`ref.*`, `meta.unresolved_entities`, political tables, ...).
- `wave7_columns(table)` parses the `broker.*` column list from the wave SQL in
  `factorlab.schema.resources.v2_sql_dir()`.

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-ingest`, `factorlab-schema`. Third party:
  `pytest>=8.0`, `requests>=2.32.3`.
- Every provider package must call `conformance_tests("<package>")` in
  `tests/test_conformance.py`; enforced by
  [test_conformance_coverage.py](../../tests/architecture/test_conformance_coverage.py).
- `FakeClickHouse` fails loudly on any query it does not recognise, so it cannot drift
  silently from what `ClickHouseSinks` / `V2ReferenceWriter` issue.

## Observability

Not applicable: test-only code with no logging contract. Failures surface as pytest
assertions naming the provider, dataset and fixture case.

## Tests

It has no `tests/` of its own (`uv run pytest libs/testkit` collects nothing). It is
exercised by its consumers: `uv run pytest providers` (conformance),
`uv run pytest libs/storage` (fake ClickHouse), and
`uv run pytest providers/ibkr components/ingest-broker` (IBKR fakes).

## Release and rollback

Never released and never shipped: no component closure includes it
([affected.py](../../tools/affected.py)), and image builds exclude the `dev` group.
A change here only affects CI and local test runs; roll back with a normal revert.

## Pitfalls

- Regenerating expectations with `regen` hides normalization regressions unless the
  diff is reviewed record by record.
- Adding a query to a sink needs a matching branch in `FakeClickHouse`, or the sink
  conformance suite fails with an unexpected-query error.
- Do not import testkit from product code: it pulls `pytest` and would break images
  built with `--no-dev`.
- `normalize` must not touch the clock or network: the offline patch catches the
  network, and determinism is checked by normalizing each capture twice.
- `test_plan_honours_capabilities` requires every `source_channel` to start with
  `<instance>:`, stricter than the engine (which also accepts the bare instance).
