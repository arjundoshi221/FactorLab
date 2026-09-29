---
id: ADR-0012
title: One uv workspace monorepo with namespace packages
status: accepted
date: 2026-09-29
status_note: Landed on restructure/platform-v3; supersedes ADR-0001, ADR-0004, ADR-0008 and ADR-0009, whose workstation layouts no longer describe how the system is built or run.
status_confirmed: false
---
# ADR-0012 — One uv workspace monorepo with namespace packages

## Context

- FactorLab was one distribution under `src/factorlab/`. Its production entry points were
  files in `scripts/` that found the code through `sys.path` edits and `parents[N]` walks
  (ADR-0008, ADR-0009). Every service ran the same image with every dependency in it. The
  root `Dockerfile` still builds that interim monolith.
- Nothing stopped a provider from importing storage, or storage from naming a vendor. These
  are the leaks listed in [ADR-0011](0011-provider-abstraction.md). A module could also import
  a package its service never declared, and nothing failed until it ran in an image without
  that package.
- The data layout decisions assumed a workstation: a local Docker database with a Railway
  auth server (ADR-0001) and a NAS mirror of repo-relative raw dumps (ADR-0004). Since the
  ClickHouse v2 cutover on 2026-09-24, production data lives in ClickHouse on the VPS, and
  raw HTTP responses live on the VPS raw archive disk (`/mnt/factorlab-data/clickhouse-raw`).
  Broker OAuth runs on Cloudflare Workers (`cloudflare/`).

## Decision

- **One uv workspace.** The root [`pyproject.toml`](../../pyproject.toml) is a virtual project
  (`package = false`), and one `uv.lock` pins third-party versions for every member. The
  members are `libs/*` (core, calendars, clickhouse, runtime, ingest, schema, storage,
  orchestration, testkit), `providers/*` (upstox, schwab, eodhd, github-csv, ibkr,
  house-clerk, congress-legislators, edgar) and `components/*` (api, ingest-india, ingest-us,
  ingest-political, ingest-broker, secrets-agent, schema-migrator). `components/web` is an
  npm project, excluded from the workspace and built by its own Dockerfile.
- **One distribution per member.** Each member has its own dependencies, is built by
  hatchling, and ships only `src/factorlab`. The namespaces `factorlab` (libraries),
  `factorlab.sources` (providers) and `factorlab.components` (components) are PEP 420
  namespaces, so there is no `__init__.py` at those levels.
- **Console scripts are the entry points.** Each component declares them in
  `[project.scripts]`, for example `factorlab-ingest-us`, `factorlab-api` and `factorlab-db`.
  `scripts/` keeps thin shims that import a package's `main`, because compose commands, cron
  wrappers and runbooks still call those paths.
- **FACTORLAB_HOME.** `factorlab.core.paths.discover_home` resolves the home from
  `FACTORLAB_HOME` (images set `/app`). Without it, the home is the checkout that contains the
  working directory, recognised by `configs/ingestion/bindings.yaml`. It is never derived from
  where a package is installed.
- **SQL is package data.** The ClickHouse v2 waves and `checksums.lock` live in
  `libs/schema/src/factorlab/schema/sql/clickhouse/v2/` and are located with
  `importlib.resources` (`factorlab.schema.resources.v2_sql_dir`).
- **Boundaries.** [`test_boundaries.py`](../../tests/architecture/test_boundaries.py) enforces:
  R1 providers never import storage; R2 storage never imports providers or components; R3
  storage holds no provider-name literals; R4 the engine (ingest, orchestration) never imports a
  concrete provider or component; R5 libraries never import a component; R6 components never
  import each other; R7 libraries import only the layers below them; R8 providers import only
  core, calendars and ingest, and never pandas, clickhouse_connect, sqlalchemy or fastapi.
- **Hygiene.** [`test_package_hygiene.py`](../../tests/architecture/test_package_hygiene.py)
  enforces: N1 no `__init__.py` in the three namespaces; N2 product code never loads `.env`,
  edits `sys.path` or walks up from `__file__`; N3 `*/legacy/` files only shrink
  (`legacy_budget.txt`); N4 direct `os.getenv`/`os.environ` reads outside `factorlab.core`
  only shrink (`env_access_allowlist.txt`); N5 every package ships `py.typed`.
- **Ratchets.** Today's R3 leaks in `libs/storage/.../v2_*.py` are listed in
  `boundary_allowlist.txt`. A new violation fails the tests, and so does a listed violation
  that has disappeared, so the lists can only shrink.
- **Isolated imports.** [`tools/check_isolated_imports.py`](../../tools/check_isolated_imports.py)
  installs each member on its own (`uv sync --frozen --no-dev --package <member>`) and imports
  every module it ships. The CI job `isolated-imports` runs it.

## Consequences

- Each image installs only its own closure, and [ADR-0013](0013-per-component-images-and-tags.md)
  builds on that. An undeclared import fails in CI, not in production.
- `uv sync --all-packages` and `uv run pytest` cover the whole workspace, with tests next to
  each member. `uv lock --check` runs in pre-commit and in CI.
- ADR-0011's layer rules are now tests. The R3 allowlist, the legacy budget and the env
  allowlist are the remaining work list. The `legacy/` packages in ingest-india, ingest-us
  and ingest-political still serve production until each provider's cutover deletes them.
- The cost is 24 Python `pyproject.toml` files, each repeating its `[tool.uv.sources]`
  entries. A new dependency has to go into the right member, which the isolated-import check
  enforces.
- The shims in `scripts/` and the root monolith `Dockerfile` stay until the compose commands
  call console scripts directly and the monolith is retired (rollout R4, feature F-007).
- This supersedes [ADR-0001](0001-local-docker-canonical-db.md), [ADR-0004](0004-nas-migration.md),
  [ADR-0008](0008-script-naming-india-historical.md) and [ADR-0009](0009-us-script-rename.md).

## Alternatives considered

- **One distribution with optional extras per service.** Rejected: extras do not stop
  cross-imports, every image still carries all the code, and there is no per-service closure
  to verify.
- **One repository per component.** Rejected: libraries change together with their callers,
  and separate repositories add version skew and cross-repository releases for a project with
  one owner.
- **Separately named top-level packages** (for example `factorlab_core`). Rejected: every
  import would have to be renamed. PEP 420 namespaces keep the existing `factorlab.*` paths.
- **Keep `scripts/` as the entry-point contract** (ADR-0008 naming). Rejected: scripts cannot
  be installed into an image on their own, and they need path hacks to find the code.
- **Plugin discovery of providers through entry points.** Rejected, as in ADR-0011. Each
  component lists its providers in a `PROVIDERS` tuple, which `tools/components.py` checks
  against the manifest and the declared dependencies.
