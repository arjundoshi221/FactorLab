# factorlab-provider-congress-legislators

> Current US legislators, committees and committee memberships from the `unitedstates/congress-legislators` JSON files, for `ref.legislators`.

## Purpose

This is a provider member with one source, `CongressLegislators` ([sources.py](src/factorlab/sources/congress_legislators/sources.py)), for dataset `ref.legislators` in market `USA`.
Importing `factorlab.sources.congress_legislators` registers it ([`__init__.py`](src/factorlab/sources/congress_legislators/__init__.py)).
The binding is `primary`. The maintained political bootstrap runs this source through the engine.

## Owns and does not own

- Owns: fetching three public JSON files, and normalizing them into `LegislatorRecord`, `CommitteeRecord` and `MembershipRecord`. That includes the chamber map and `normalize_role()` (chair, vice_chair, ranking_member, member).
- Does not own: the `legislator_name` alias used to resolve House filers (the DB service derives it with `identifiers.legislator_name_key`), filings and trades ([house-clerk](../house-clerk/CONTEXT.md)), ClickHouse writes, or the production bootstrap (`components/ingest-political/bootstrap.py`).

## Entry points

- `CongressLegislators` (`provider = "congress_legislators"`, `dataset = "ref.legislators"`). `plan` makes one unit per file, in the fixed order `FILES = ("legislators", "committees", "memberships")`. `params.files` can narrow the list, but not reorder it.
- Run it through the component: `factorlab-ingest-political engine run --dataset ref.legislators --market USA --dry-run`.

## Configuration and secrets

`CongressLegislatorsSettings` loads `configs/sources/congress_legislators.yaml`. That file does not exist, so the code defaults apply.

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `base_url` | - | `https://unitedstates.github.io/congress-legislators` | |
| `files.legislators` | - | `legislators-current.json` | |
| `files.committees` | - | `committees-current.json` | |
| `files.memberships` | - | `committee-membership-current.json` | |
| `timeout` | - | 60.0 | |

There are no secrets and no auth. The `legislators` block in [configs/sources/political.yaml](../../configs/sources/political.yaml) (raw.githubusercontent YAML files, including historical and `executive.yaml`) documents the legacy sources. This package does not read it.

## Data

- `source` is `congress_legislators`. `source_channel` is `<instance>:legislators`, `<instance>:committees` or `<instance>:memberships`. Unit names are `file:<name>`.
- Binding in [bindings.yaml](../../configs/ingestion/bindings.yaml): `{dataset: ref.legislators, market: USA, provider: congress_legislators, role: primary}`
- The dataset tables are `ref.entities`, `ref.identifier_aliases`, `ref.legislator_terms`, `alt.political_committees` and `alt.political_committee_memberships`. None has `source` in its key, so a shadow run normalizes and counts only.
- Legislators and members use `EntityRef("bioguide", <id>, "person_legislator")`. A legislator with no bioguide id or no house/senate term is skipped. Subcommittee codes are the parent `thomas_id` followed by the subcommittee `thomas_id`.

## Dependencies and contracts

- Workspace: only `factorlab-ingest` (datasets, political records, `congress_number`, errors, `raise_for_status`). Third-party: `pydantic`, `requests`.
- R1/R8 ([test_boundaries.py](../../tests/architecture/test_boundaries.py)): no storage, no other provider, no dataframe, DB or web stack.
- `normalize` is pure over the capture. The congress number comes from `capture.fetched_at.date()`, not the wall clock, so a replay tags the same congress. An explicit `params.congress` override is archived in capture metadata and takes precedence.
- Records have no provider alias (`requires_alias=False` for this dataset). Identity is the bioguide `EntityRef`.

## Observability

- The module does no logging of its own. Engine logs use pipeline `ref.legislators:congress_legislators`. The engine writes `meta.ingestion_runs` and `raw.archive`.
- The `ingest-political` health check is `exit-zero`. Use the `factorlab-logs` and `factorlab-clickhouse` skills.

## Tests

`uv run pytest providers/congress-legislators`

- [test_conformance.py](tests/test_conformance.py) runs the shared kit against `tests/fixtures/ref.legislators/` (`legislators`, `committees`, `memberships` captures plus `requests.json`).
- Regenerate expectations after an intentional change: `uv run python -m factorlab.testkit.conformance regen congress_legislators`.

## Release and rollback

- A provider is never released alone. It ships inside the `ingest-political` image ([component.yaml](../../components/ingest-political/component.yaml) `providers: [house_clerk, congress_legislators]`, [providers.py](../../components/ingest-political/src/factorlab/components/ingest_political/providers.py)). Rollback class is `writer`.
- `ingest-political bootstrap`, scheduled `15 2 * * *`, collects this source before filings and trades in one job run. The CLI retains its `--congress` override.
- Bindings changes are config, not code. `configs/` is baked into the image, so they ship with an `ingest-political` image. Cutover follows 07 §15.2.

## Pitfalls

- The file order matters. Memberships reference both legislators and committees, so do not reorder `FILES`.
- This provider owns committee-role normalization; the retired storage normalizer has been removed.
- Fetch errors are isolated by the engine; a failed reference stage defers downstream filings and trades.
