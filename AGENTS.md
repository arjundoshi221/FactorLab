# FactorLab instructions for Codex

Use this file as the repository entry point on every task. Check the relevant source files and linked runbooks before changing behavior; repository state and executable configuration are authoritative when docs disagree.

## Working workflow

1. Read this file and inspect `git status --short` before editing. Preserve existing user changes; do not revert, stage, or overwrite unrelated work.
2. Trace the requested behavior through its callers, storage/API boundaries, configuration, and relevant docs. Keep provider-specific behavior at source adapters and preserve point-in-time and raw-data guarantees.
3. Make the smallest coherent change. Update the relevant documentation when a workflow, schema, deployment topology, or operator-visible behavior changes.
4. Run focused checks when requested or needed to establish correctness. Common commands are `python -m pytest <path>`, `python -m ruff check <path>`, and, from `src/factorlab/webui`, `npm test` and `npm run build`.
5. Review the final diff and status. Never include `.env`, credentials, private keys, ingested data, logs, database files, or generated archives in changes.

Do not launch long-running ingestion, migration, production, or other remote operations unless the user asked for that operation. Keep credentials out of command output, docs, and commits.

## Repository map

- `src/factorlab/`: Python application. `api/` serves HTTP endpoints; `sources/` contains provider adapters; `storage/` handles persistence; `core/` and `shared/` hold common models, configuration, and utilities; `pipeline/` and `compute/` contain processing and research logic; `countries/` and `universe/` define market-specific behavior; `webui/` is the React/TypeScript interface.
- `tests/`: Python tests, including API, ingestion, universe, and ClickHouse migration coverage. Frontend tests live alongside the UI under `src/factorlab/webui/`.
- `configs/`: source and universe configuration. Keep secrets in environment-managed secret stores, never in checked-in config.
- `sql/` and `migrations/`: ClickHouse SQL and database migrations. Read the migration runbook before changing or applying production migrations.
- `deploy/`: production Compose bundle, release helper, and deployment/rollback scripts. `deploy/README.md` is the release and server operations guide.
- `cloudflare/`: Cloudflare Workers for Upstox authentication and OAuth callback handling. Each Worker has its own README and package scripts.
- `docs/architecture/`: system and schema design. `docs/data-sources/`: provider and domain references. `docs/operations/`: operating runbooks. `docs/developments/`: development plans. `docs/security/`: security policy and reviews.
- `scripts/`: ingestion, administration, migrations, verification, and repository checks. Inspect a script's help and source before running it.
- `data/` and `logs/`: local runtime data and output; do not commit them. `.env*` files are sensitive and must not be read into reports or committed.
Keep the repository root for maintained project files. Put task scratch scripts and temporary outputs under the ignored `.tmp/<task-name>/` directory, and remove them when that task is complete. Do not create ad hoc `.sh`, `.ps1`, logs, or generated bundles in the root. Docker image exports are disposable transfer artifacts, not source: create them only when an explicitly requested workflow needs one, store them outside the repository (or temporarily under `.tmp/docker-images/`), and remove them after the transfer is verified. Never commit image archives.

For system and schema questions, consult `docs/architecture/`. For environment variables and live operations, consult `docs/operations/`. For deployment, read `deploy/README.md`; for the VPS topology and service state, read `docs/VPS.md`.

For user-requested live ClickHouse data questions, use `python scripts/read_clickhouse.py --sql "SELECT ..."` (or `--sql-file`) and return only the relevant compact results. See `docs/VPS.md` for one-time SSH key setup. Do not run live queries without a user request.

## Production image and runtime

The production image is built from the repository `Dockerfile`. Its Node 22 build stage installs the web UI from `package-lock.json` and compiles it to `dist/`. Its Python 3.12 runtime stage installs the FactorLab package and copies in `src/`, `scripts/`, `configs/`, `sql/`, and the compiled UI at `/app/web-dist`. `FACTORLAB_WEB_DIST` points the API at the compiled UI. The image is shared by the API and ingestion services; Compose supplies a service-specific command for each one. Some services can use distinct image pins, so preserve the per-service image variables in `deploy/compose.production.yml`.

At runtime, Compose starts the Cloudflare secrets agent, which writes fetched runtime credentials into memory-backed volumes. ClickHouse uses persistent host mounts for curated and raw data. The one-shot bootstrap applies ClickHouse setup before ingestion and API services start. The API and ClickHouse ports bind to VPS loopback and are accessed through an SSH tunnel. Read `deploy/compose.production.yml` and `docs/VPS.md` for the authoritative services, ports, mounts, and dependency order; never put secret values in the image or deployment bundle.

## Development and verification

Python requires 3.12 or newer. Install the project and development tools with `python -m pip install -e ".[dev,notify]"`. The CI backend check is `python -m pytest`. Use focused tests for a narrow change, and do not claim a check passed unless it was run.

The web UI uses Node 22 and npm. From `src/factorlab/webui`, install from the lockfile with `npm ci`; run `npm test` and `npm run build` for UI changes. `npm run dev` starts the local Vite server.

Pre-commit hooks enforce secret, data-path, key, size, YAML/JSON, and whitespace checks. See `.pre-commit-config.yaml` and `docs/CONTRIBUTING.md`. Do not bypass a hook to make a change pass.

## Production deployment

Use the repository deployment script as the sole release entry point: from the repo root on Windows, run `./deploy/release.ps1` in PowerShell. Do not reproduce its release steps manually in chat or by running Docker, SSH, image-build, or tag commands yourself. The script requires a clean worktree on `main` exactly synchronized with `origin/main`; it pushes an immutable release tag. That tag starts `.github/workflows/release.yml`, which runs Python and UI checks, builds the Linux/AMD64 image, publishes it to private GHCR, and deploys the exact image digest to the VPS. The workflow verifies the services and attempts rollback if deployment checks fail.

Before invoking or modifying a release, read `deploy/README.md` and inspect the current worktree. Do not move or reuse release tags. Production schema migrations are separate reviewed, forward-only operations and are not run by the release workflow. After a deployment, use the runbook to inspect the GitHub Actions summary and VPS health; do not infer success just because the release script printed that the workflow started.

Do not deploy, create release tags, run production migrations, or operate on the VPS unless the user explicitly requests that operation. Never expose ClickHouse or the private hub directly to the public internet. Do not disable SSH host-key checking. Production secrets belong in the configured secret stores and runtime secret volumes; do not copy them into source files or logs.
