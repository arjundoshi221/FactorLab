# factorlab-provider-github-csv

> Index constituents (today the S&P 500) from public CSV files on GitHub, for `ref.universe_membership`.

## Purpose

This is a provider member with a single source, `GithubCsvUniverse` ([sources.py](src/factorlab/sources/github_csv/sources.py)). It turns an index CSV of bare tickers into a complete membership snapshot for market `USA`.
Importing `factorlab.sources.github_csv` registers it ([`__init__.py`](src/factorlab/sources/github_csv/__init__.py)).
The binding is `shadow`. At the P4 cutover it replaces the legacy `universe/github_csv.py` plus Schwab validation.

## Owns and does not own

- Owns: fetching the configured CSV with a host allow-list, symbol normalization (`BRK.B` / `BRK/B` -> `BRK-B`), and the constituent-minimum check.
- Does not own: resolving tickers to listings (the DB service resolves them within the country and parks misses, [07 §8.1](../../docs/architecture/07-ingestion-provider-abstraction.md)), the snapshot open/close logic and the 20%-drop guard (`factorlab-ingest` `universe_snapshot.py`), or the production universe job (`components/ingest-us` legacy `universe/`).

## Entry points

- `GithubCsvUniverse` (`provider = "github_csv"`, `dataset = "ref.universe_membership"`).
- Helpers `normalize_symbol()` and `validate_url()`, and the settings models `GithubCsvSettings` / `CsvIndex`, all in [sources.py](src/factorlab/sources/github_csv/sources.py).
- Run it through the component: `factorlab-ingest-us engine run --dataset ref.universe_membership --market USA --instance github_csv --dry-run`.

## Configuration and secrets

`GithubCsvSettings` loads [configs/sources/github_csv.yaml](../../configs/sources/github_csv.yaml). There is no separate `settings.py`.

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `timeout` | - | 30.0 | The YAML also sets 30 |
| `indexes.<code>.url` | - | none | Must be `https` on `github.com` or `raw.githubusercontent.com`, and so must the final URL after redirects |
| `indexes.<code>.symbol_column` | - | `Symbol` | |
| `indexes.<code>.minimum_constituents` | - | required, at least 1 | The YAML sets `sp500` to 450 |
| `indexes.<code>.name`, `country` | - | `""`, `US` | |

There are no secrets and no auth. `CsvIndex` is `extra="forbid"`.

## Data

- `source` is `github_csv`, or `github_csv:shadow` while shadow. `source_channel` is `<instance>:csv`. Unit names are `universe:<code>`.
- Binding in [bindings.yaml](../../configs/ingestion/bindings.yaml): `{dataset: ref.universe_membership, market: USA, provider: github_csv, role: shadow, priority: 10}  # -> primary`
- The YAML's `sp500.url` is `https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv`.
- Records are `ConstituentRecord(code, InstrumentRef("", "", "", symbol, country))`, which carry no provider alias. The dataset writes `ref.universes` and `ref.universe_membership`, but `ref.*` gets no writes while shadow.

## Dependencies and contracts

- Workspace: only `factorlab-ingest`. There is no `factorlab-core` dependency, so importing `factorlab.core` here would fail [check_isolated_imports.py](../../tools/check_isolated_imports.py). Third-party: `pydantic`, `requests`.
- R1/R8 ([test_boundaries.py](../../tests/architecture/test_boundaries.py)): no storage, no other provider, no dataframe, DB or web stack.
- `normalize` is pure. It decodes the body as `utf-8-sig`, and the capture metadata carries the index config so a replay applies the same column and minimum.

## Observability

- The module does no logging of its own. Failures surface as unit failures in the engine log under pipeline `ref.universe_membership:github_csv`, and in `meta.ingestion_runs`.
- Use the `factorlab-logs` and `factorlab-clickhouse` skills to read them.

## Tests

`uv run pytest providers/github-csv`

- [test_conformance.py](tests/test_conformance.py) runs the shared kit against `tests/fixtures/ref.universe_membership/` (`sp500_constituents`, `requests.json`).
- Regenerate expectations after an intentional change: `uv run python -m factorlab.testkit.conformance regen github_csv`.

## Release and rollback

- A provider is never released alone. It ships inside the `ingest-us` image ([component.yaml](../../components/ingest-us/component.yaml) `providers: [schwab, eodhd, github_csv, edgar]`, [providers.py](../../components/ingest-us/src/factorlab/components/ingest_us/providers.py)). Rollback class is `writer`.
- Production is still `ingest-us universe` through [legacy/universe/github_csv.py](../../components/ingest-us/src/factorlab/components/ingest_us/legacy/universe/github_csv.py). That job reads the mounted `us-universe.yaml`, not this provider's config.
- Bindings and `configs/sources` changes are config, not code. `configs/` is baked into the image, so they ship with an `ingest-us` image. Cutover follows 07 §15.2.

## Pitfalls

- One unusable symbol, a missing column or a count below the minimum raises `NormalizationError`, and the whole snapshot fails. That is deliberate, because a partial snapshot would close real memberships.
- Symbols must match `^[A-Z0-9][A-Z0-9-]{0,13}$` after normalization. A trailing `.US` is stripped.
- Keep `configs/sources/github_csv.yaml` and `deploy/us-universe.yaml` in step until cutover. The YAML header says it mirrors that file.
