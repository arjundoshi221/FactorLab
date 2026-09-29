# Repository layout

> Status: `[current]`. Decision records: [ADR-0012](../decisions/0012-uv-workspace-monorepo.md)
> (workspace), [ADR-0013](../decisions/0013-per-component-images-and-tags.md) (images and
> tags), [ADR-0015](../decisions/0015-host-deployer-and-rollback-classes.md) (deployer).

FactorLab is one [uv](https://docs.astral.sh/uv/) workspace: one `pyproject.toml` at the
root (virtual, never installed), one `uv.lock`, and one distribution per member. Every
member ships its own package under a shared PEP 420 namespace, and every deployable
component builds its own image from its own dependency closure.

```text
pyproject.toml  uv.lock  .python-version  AGENTS.md  CLAUDE.md
libs/<x>/            factorlab-<x>                  -> factorlab.<x>
providers/<p>/       factorlab-provider-<p>         -> factorlab.sources.<p>
components/<c>/      factorlab-component-<c>        -> factorlab.components.<c>   (web: npm)
deploy/              the platform unit: compose base, host deployer, scripts, host config
cloudflare/<w>/      Cloudflare Workers (npm)
configs/             bindings, source settings, universes (copied into ingest images)
tools/               repository tooling (+ templates/ for the scaffolder)
tests/               cross-cutting tests: architecture rules, deploy, tools
docs/                architecture, decisions, features, sprints, operations, data sources
```

Each member directory holds `pyproject.toml`, `src/factorlab/...` (with `py.typed`),
`tests/`, and `CONTEXT.md` with `CLAUDE.md` / `AGENTS.md` pointers. A component adds
`component.yaml`, `Dockerfile` (+ `Dockerfile.dockerignore`), `deploy/compose.yaml` and
`CHANGELOG.md`. Code still running production ahead of its engine cutover lives in
`components/<c>/src/factorlab/components/<c>/legacy/` and may only shrink.

## Members

| Group | Members |
|---|---|
| libs | `core` (settings, secrets, paths, logging, build), `runtime` (exit codes, heartbeat, lock, signals, notify), `calendars`, `clickhouse`, `schema` (v2 migrations as package data, `factorlab-db`), `ingest` (provider contract, registry, bindings, engine, datasets), `storage` (sinks, writers), `orchestration` (engine CLI), `testkit` (dev only) |
| providers | `upstox`, `schwab`, `eodhd`, `github-csv`, `ibkr`, `house-clerk`, `congress-legislators`, `edgar` |
| components | `api`, `web`, `ingest-india`, `ingest-us`, `ingest-political`, `ingest-broker`, `secrets-agent`, `schema-migrator` |
| release units | the components, `platform` (deploy/), `upstox-auth-worker`, `upstox-oauth-callback-worker` |

## Dependency rules

Enforced by `tests/architecture/` (AST scans with shrink-only allowlists):

| Rule | |
|---|---|
| R1 | providers never import `factorlab.storage` |
| R2 | storage never imports providers or components |
| R3 | no provider-name literals in storage (except `KNOWN_ALIAS_KINDS`) |
| R4 | ingest and orchestration load providers only by name, never import them or a component |
| R5 | libraries never import a component |
| R6 | components never import each other |
| R7 | library layers: `core` < `calendars`, `clickhouse`, `runtime` < `ingest` < `schema`, `storage` < `orchestration` |
| R8 | providers import only `core`, `calendars`, `ingest`; never another provider, pandas, clickhouse_connect, sqlalchemy or fastapi |
| N1–N5 | PEP 420 namespaces (no `__init__.py` in `factorlab`, `factorlab.sources`, `factorlab.components`); no dotenv, `sys.path` edits or `__file__` walks in product code; legacy only shrinks; direct env reads only in `factorlab.core`; every package ships `py.typed` |

Each member's declared dependencies must cover its imports: CI installs every member alone
and imports all its modules (`tools/check_isolated_imports.py`), and verifies each image's
installed packages against `uv export` (`tools/verify_image.py`).

## Configuration

Typed settings (`factorlab.core.settings.FactorLabSettings`) read init arguments, then
secrets by name (`Secret("NAME")` via the runtime secret volumes), then the environment,
then defaults; nothing reads `.env` at runtime (local development: `uv run --env-file .env`).
`FACTORLAB_HOME` is the root for paths (`/app` in images; the checkout otherwise).

## Recipes

| Change | What you touch |
|---|---|
| Add a provider | `uv run python tools/scaffold.py new-provider <p> --dataset <id> --market <ISO3> --component <c> --bind`, then implement `sources.py`, record fixtures, name secrets ([skill](../../.claude/skills/new-provider/SKILL.md)) |
| New pipeline for an existing provider | a binding in `configs/ingestion/bindings.yaml` (+ a compose service or engine command in the component) |
| Switch or duplicate a provider | roles and priorities in `configs/ingestion/bindings.yaml` (primary / secondary / shadow), `configs/sources/<p>.yaml`; released with the component (configs ship in its image) |
| New dataset (the only change that needs DB work) | dataset module in `libs/ingest`, DDL in 06, a new schema wave (`tools/schema_checksums.py add`), a sink + conformance tests in `libs/storage`, then a provider |
| Add a component | `uv run python tools/scaffold.py new-component <c> [--writer]`, secrets volume in `deploy/compose.base.yml` if needed ([skill](../../.claude/skills/new-component/SKILL.md)) |
| Add a library | `uv run python tools/scaffold.py new-lib <x> --depends-on core,...` |
| Release a unit | `deploy/release.ps1 -Component <unit> -Bump ...` ([releases](../operations/releases.md)) |
| Plan work | a feature in `docs/features/`, scheduled with `sprint:` ([skill](../../.claude/skills/feature/SKILL.md)) |

## Tooling

| Tool | Purpose |
|---|---|
| `tools/components.py` | manifests: `check`, `list`, `matrix`, `render-compose`, `version`, `bundle` |
| `tools/affected.py` | which release units a change affects (closure from the workspace graph + uv.lock) |
| `tools/release.py` | version bump, changelog, tag check (behind `deploy/release.ps1 -Component`) |
| `tools/scaffold.py` | `new-provider`, `new-component`, `new-lib` |
| `tools/check_docs.py` | features, sprints, decisions, CONTEXT.md, links; `--write` regenerates indexes and the roadmap |
| `tools/schema_checksums.py` | forward-only migration lock |
| `tools/read_logs.py` | production logs through the restricted log reader |
| `tools/verify_image.py`, `tools/check_isolated_imports.py` | image and member closure checks |
| `tools/hooks/` | pre-commit checks (data paths, plaintext secrets, raw paths) |
