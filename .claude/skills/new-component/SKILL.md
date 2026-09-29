---
name: new-component
description: Add a new independently released FactorLab component (its own image, compose service, logs and release tags) with the scaffolder. Use when new work needs its own long-running service or job rather than a change to an existing component.
---

# Adding a component

A component is a deployable unit: `components/<name>/` with a manifest
(`component.yaml`), a Dockerfile, a compose fragment and its own `<name>/vX.Y.Z` tags.
Prefer extending an existing component unless the new work has a different runtime,
release cadence or blast radius. See [docs/architecture/08-repository-layout.md](../../../docs/architecture/08-repository-layout.md)
and [docs/decisions/0013-per-component-images-and-tags.md](../../../docs/decisions/0013-per-component-images-and-tags.md).

## Steps

1. Scaffold (runs `uv lock`, `uv sync`, renders the compose file, checks manifests and runs the tests):
   ```bash
   uv run python tools/scaffold.py new-component <name> [--writer] --summary "..."
   ```
   `--writer` for anything that writes production data: rollback class `writer` with
   `data_contract: 1` (bump it whenever written data changes; the deployer then refuses
   an automatic rollback across it).
2. It wires: root `[tool.uv.sources]`, `deploy/scripts/prepare-host.sh` (log directory),
   `deploy/logrotate/factorlab`, `CHANGELOG.md`, `CONTEXT.md` and pointers, and
   `deploy/compose.production.yml`. CI's image matrix picks the Dockerfile up automatically.
3. Adjust the manifest (`services`, `health`, `secrets`) and the fragment (environment,
   runtime secrets volume from `deploy/compose.base.yml`, memory limit). Fragment policy:
   ports bind `127.0.0.1` only, no privileged keys, bind mounts only under
   `/var/log/factorlab/`, `/var/lib/factorlab/app-data`, `/etc/factorlab/`.
4. Add providers with the `new-provider` skill (`--component <name>`).
5. Check:
   ```bash
   uv run pytest components/<name> tests/architecture tests/deploy
   uv run python tools/components.py check && uv run python tools/check_docs.py
   docker build -f components/<name>/Dockerfile -t factorlab-<name>:dev .
   uv run python tools/verify_image.py <name> factorlab-<name>:dev
   ```
6. Fill in `CONTEXT.md`, add a feature file for the work, and release with the
   `release-component` skill. Until the platform bootstrap (rollout R2) is done, do not
   cut a monolith release: it would try to start the new service.
