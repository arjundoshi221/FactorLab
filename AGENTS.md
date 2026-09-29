# FactorLab instructions for coding agents

Use this file as the repository entry point on every task (Codex, Claude Code and any other
agent). Check the relevant source files and linked runbooks before changing behavior;
repository state and executable configuration are authoritative when docs disagree.

## Working workflow

1. Read this file and inspect `git status --short` before editing. Preserve existing user changes; do not revert, stage, or overwrite unrelated work.
2. Before changing a workspace member, read its `CONTEXT.md` (every library, provider, component, `deploy/` and each Cloudflare Worker has one). Trace the requested behavior through its callers, storage/API boundaries, configuration, and relevant docs. Keep provider-specific behavior in providers and preserve point-in-time and raw-data guarantees.
3. Make the smallest coherent change. Update the member's `CONTEXT.md` and the relevant documentation in the same change when a workflow, schema, deployment topology, or operator-visible behavior changes.
4. Run focused checks when requested or needed to establish correctness (see *Development and verification*). Do not claim a check passed unless it was run.
5. Review the final diff and status. Never include `.env`, credentials, private keys, ingested data, logs, database files, or generated archives in changes.

Do not launch long-running ingestion, migration, production, or other remote operations unless the user asked for that operation. Keep credentials out of command output, docs, and commits.

## Repository map

One uv workspace (`pyproject.toml`, `uv.lock`); the full layout, dependency rules and recipes are in [docs/architecture/08-repository-layout.md](docs/architecture/08-repository-layout.md).

- `libs/`: shared libraries, `factorlab-<x>` → `factorlab.<x>`: `core` (settings, secrets, paths, logging), `runtime`, `calendars`, `clickhouse`, `schema` (ClickHouse v2 migrations as package data), `ingest` (the provider contract and engine), `storage` (sinks and writers), `orchestration` (engine CLI), `testkit` (dev only).
- `providers/`: data providers, `factorlab-provider-<p>` → `factorlab.sources.<p>`; they import only core, calendars and ingest.
- `components/`: deployable units, each with `component.yaml`, a Dockerfile, a compose fragment and its own releases: `api`, `web` (npm), `ingest-india`, `ingest-us`, `ingest-political`, `ingest-broker`, `secrets-agent`, `schema-migrator`. Legacy production code awaiting its engine cutover lives in `components/*/src/**/legacy/` and may only shrink.
- `deploy/`: the platform unit: compose base, host deployer and scripts, logrotate/systemd/sshd config. `deploy/README.md` is the release and server operations guide.
- `cloudflare/`: Cloudflare Workers for Upstox authentication and the OAuth callback.
- `configs/`: bindings (`configs/ingestion/bindings.yaml`), source settings and universes. Keep secrets in environment-managed secret stores, never in checked-in config.
- `tools/`: repository tooling (components, release, affected, scaffold, check_docs, read_logs, schema_checksums, hooks). `tests/`: cross-cutting tests (architecture rules, deploy, tools); each member's own tests live in its `tests/`.
- `scripts/`: thin compatibility shims and `read_clickhouse.py`; business logic lives in the members.
- `docs/`: `architecture/` (system and schema design), `decisions/` (ADRs), `features/` and `sprints/` (planning), `operations/` (runbooks), `data-sources/`, `security/`.
- `data/` and `logs/`: local runtime data and output; do not commit them. `.env*` files are sensitive and must not be read into reports or committed.

Keep the repository root for maintained project files. Put task scratch scripts and temporary outputs under the ignored `.tmp/<task-name>/` directory, and remove them when that task is complete. Do not create ad hoc `.sh`, `.ps1`, logs, or generated bundles in the root. Docker image exports are disposable transfer artifacts, not source: create them only when an explicitly requested workflow needs one, store them outside the repository (or temporarily under `.tmp/docker-images/`), and remove them after the transfer is verified. Never commit image archives.

For system and schema questions, consult `docs/architecture/`. For environment variables and live operations, consult `docs/operations/`. For deployment, read `deploy/README.md`; for the VPS topology and service state, read `docs/VPS.md`.

## Planning and decisions

Work is planned as features in `docs/features/` (from `_template.md`), scheduled into two-week sprints in `docs/sprints/`, and architectural choices are recorded in `docs/decisions/`. After editing any of them run `uv run python tools/check_docs.py --write`; CI runs `tools/check_docs.py`. Features with a `roadmap:` block drive the hub's `/roadmap` page.

## Live data and logs

For user-requested live ClickHouse data questions, use `uv run python scripts/read_clickhouse.py --sql "SELECT ..."` (or `--sql-file`) and return only the relevant compact results. For user-requested production logs, use `uv run python tools/read_logs.py` (`list`, `tail`, `errors`, `run --run-id`) through the restricted `factorlab-logs` account (`docs/operations/log-access.md`). Do not run live queries or log reads without a user request.

## Development and verification

Python 3.12 and [uv](https://docs.astral.sh/uv/). Install everything with `uv sync --all-packages`. Checks (all run in CI; `ci-ok` is the single required check):

- `uv run pytest` (focused: `uv run pytest libs/<member>` etc.)
- `uv run ruff check .` and `uv run ruff format --check .`
- `uv run basedpyright` (strict on libs; existing findings are baselined, new ones fail)
- `uv run python tools/components.py check` and `render-compose --check`
- `uv run python tools/schema_checksums.py check` (applied migrations are frozen)
- `uv run python tools/check_docs.py`

The web UI uses Node 22 and npm: from `components/web`, `npm ci`, `npm test`, `npm run build`. Pre-commit hooks (`uv run pre-commit install --install-hooks`) enforce secret, data-path, lint, format, lock, manifest and architecture checks. Do not bypass a hook to make a change pass.

New members come from the scaffolder, not by hand: `uv run python tools/scaffold.py new-provider|new-component|new-lib <name>` (see the `new-provider` and `new-component` skills).

## Production deployment

Releases are per unit: from a clean `main` exactly synchronized with `origin/main`, run `./deploy/release.ps1 -Component <unit> -Bump patch|minor|major` in PowerShell (add `-DryRun` first). It pushes an immutable `<unit>/vX.Y.Z` tag that starts `.github/workflows/component-release.yml`: tests, image build and verification, then the host deployer, which recreates only that unit's services and recovers by its rollback class. Until the per-component rollout completes (docs/operations/rollout.md), production still runs the monolith path: `./deploy/release.ps1` without `-Component` pushes a `release/*` tag for `.github/workflows/release.yml`. Do not reproduce release steps manually in chat or by running Docker, SSH, image-build, or tag commands yourself.

Before invoking or modifying a release, read `deploy/README.md`, the unit's `CONTEXT.md` and `docs/operations/releases.md`, and inspect the current worktree. Do not move or reuse release tags. Production schema migrations are separate reviewed, forward-only operations and are not run by the release workflow. After a deployment, use the runbook to inspect the GitHub Actions summary and VPS health; do not infer success just because the release script printed that the workflow started.

Do not deploy, create release tags, run production migrations, or operate on the VPS unless the user explicitly requests that operation. Never expose ClickHouse or the private hub directly to the public internet; the hub has no in-app login, so Cloudflare Access through the tunnel is its only authentication and must not be bypassed by a public port or reverse proxy. Do not disable SSH host-key checking. Production secrets belong in the configured secret stores and runtime secret volumes; do not copy them into source files or logs.
