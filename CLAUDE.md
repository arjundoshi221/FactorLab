@AGENTS.md

# Claude Code specifics

Everything in AGENTS.md applies; this adds onboarding and what is specific to Claude Code.

## Start here (new contributors)

`main` is the only long-lived branch. Work on a short branch or directly on `main`, push,
and let CI (`ci-ok`) gate it.

```bash
git clone https://github.com/arjundoshi221/FactorLab.git && cd FactorLab
uv sync --all-packages                        # Python 3.12 workspace + dev tools
uv run pre-commit install --install-hooks     # required; the hooks enforce repo policy
uv run pre-commit install --hook-type pre-push
cd components/web && npm ci                   # Node 22, for the hub UI
```

Windows: if `uv` is not on PATH it is usually in
`%APPDATA%\Python\Python3xx\Scripts`. Some files are committed with CRLF; keep each file's
existing line endings so diffs stay small.

Before pushing, run what CI runs for the parts you touched (full list in AGENTS.md):
`uv run pytest <member>`, `uv run ruff check . && uv run ruff format --check .`,
`uv run basedpyright`, and for the UI `npm test && npm run build` in `components/web`.

Then read [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md), the
[repository layout](docs/architecture/08-repository-layout.md) and the `CONTEXT.md` of the
member you are changing.

## Editing the UI

The hub is two units: `components/web` (React + Vite SPA, static files behind nginx) and
`components/api` (FastAPI, `/hub/api/v1/*`). Read [components/web/CONTEXT.md](components/web/CONTEXT.md)
first: pages live in `src/pages/`, the terminal shell and nav in `src/shell/`, colours and
spacing are tokens in `src/theme/tokens.css` (never raw hex in page CSS), charts in
`src/shared/charts/`.

Local loop:

```bash
cd components/web
npm run dev        # http://127.0.0.1:5173, proxies /hub/api to 127.0.0.1:8000
npm test           # vitest; page tests stub fetch, so no API is needed
```

For real data, tunnel the VPS's `api-candidate` (the API that serves the hub, on host port
18000) to local 8000: `ssh -L 8000:127.0.0.1:18000 ubuntu@<VPS host>` (host and access in
[docs/VPS.md](docs/VPS.md); needs an SSH key on the server). A new API route goes in
`components/api` with a test, and its response type in `components/web/src/`.

**Deploying is automatic.** A push to `main` that changes `components/web/`,
`components/api/`, `libs/`, `configs/`, `pyproject.toml` or `uv.lock` runs
[branch-ui-deploy.yml](.github/workflows/branch-ui-deploy.yml): it waits for CI on that
commit, builds both images, replaces `api-candidate` then `web` on the VPS, verifies them,
and restores the preceding images on any failure. Watch it under GitHub → Actions →
"Branch UI trial release"; the job summary reports verification and recovery. A red CI
means no deploy. To redeploy without a code change, run `./deploy/release-ui-trial.ps1`
from a clean `main` that matches `origin/main`. Details: [deploy/testing/README.md](deploy/testing/README.md).

## Creating a new container

A container is a component (`components/<name>/`: `component.yaml`, Dockerfile, compose
fragment) or an extra service inside an existing component's image. Prefer adding a
service to an existing component when it shares its code and image; make a new component
only for a different runtime, release cadence or blast radius.

1. **Scaffold** (never by hand): `uv run python tools/scaffold.py new-component <name>
   [--writer] --summary "..."`. Then follow the `new-component` skill
   ([.claude/skills/new-component/SKILL.md](.claude/skills/new-component/SKILL.md)) to
   fill in the manifest, the compose fragment (loopback-only ports, no privileged keys)
   and `CONTEXT.md`. For a new service in an existing component, add it to that
   component's `component.yaml` `services` and `deploy/compose.yaml`.
2. **Render and check**: `uv run python tools/components.py render-compose` regenerates
   `deploy/compose.production.yml`; then `tools/components.py check`,
   `uv run pytest components/<name> tests/architecture tests/deploy`, and a local
   `docker build -f components/<name>/Dockerfile .` plus `tools/verify_image.py`.
3. **Monitoring**: add the service to `deploy/monitor-services.yaml` (silence deadline) so
   the monitor alerts when it stops reporting.
4. **Schema**: if it writes new tables, add a migration wave (see CONTRIBUTING) and apply
   it on the VPS before the first deploy; deploys never migrate.
5. **Deploy**: per-component releases (`release-component` skill) are not active until the
   platform bootstrap ([docs/operations/rollout.md](docs/operations/rollout.md)). Until
   then a new service ships as an ingest or platform trial scope. Register the scope in
   all of: `deploy/testing/ingest_trial.py` (`UNITS`) and, for a service inside another
   unit's image, `ingestion_test.py` (`SERVICE_SCOPES`), or `platform_trial.py`
   (`SCOPES`); the tag regex in `.github/workflows/branch-ingest-deploy.yml` or
   `branch-platform-deploy.yml`; and the `-Scope` lists in `deploy/release-ui-trial.ps1`.
   Add a test beside `tests/deploy/test_ingest_trial.py`. After the commit is on `main`
   and CI is green: `./deploy/release-ui-trial.ps1 -Trial ingest -Scope <name>` (or
   `-Trial platform`). `futures-us` is a worked example: grep for it.

## Claude Code tooling

- **Member context loads itself.** Each member directory has a `CLAUDE.md` that imports its
  `CONTEXT.md`, so reading files in `libs/<x>/`, `providers/<p>/` or `components/<c>/`
  brings in that member's context. Keep those files current in the same change.
- **Skills** (`.claude/skills/`): `feature` (write or update docs/features), `new-provider`,
  `new-component`, `release-component` (user-invoked only; a release deploys to production),
  `factorlab-logs` and `factorlab-clickhouse` (live production reads, only on request), and
  `grill-me` (stress-test a plan).
- **Agents** (`.claude/agents/`): `product`, `sprint`, `developer`, `bug-hunter`, `docs` and
  `clickhouse-steward`; see `.claude/agents/README.md` for who owns what.
- **Planning** lives in the repo, not in chat: features in `docs/features/`, the active
  sprint in `docs/sprints/`, decisions in `docs/decisions/`. Record outcomes there.
- **Scratch work** goes under `.tmp/<task-name>/` and is removed when the task is done.
  Use `git worktree` under `.tmp/` for parallel work and remove it when merged; do not
  leave long-lived side branches.
