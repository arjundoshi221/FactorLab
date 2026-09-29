---
id: ADR-0015
title: Host deployer with per-component rollback classes
status: accepted
date: 2026-09-29
status_note: Built on restructure/platform-v3 but not yet active on the VPS; it takes over from deploy-release.sh at the first platform release, step R2 of feature F-007.
status_confirmed: false
---
# ADR-0015 — Host deployer with per-component rollback classes

## Context

- The monolith release (`deploy-release.sh`) recreated every managed service from one
  generated `compose.production.yml`, and rolled everything back together. With one image
  per component ([ADR-0013](0013-per-component-images-and-tags.md)), a release has to change
  one component and leave the rest running.
- Components differ in what a rollback means. Restoring the previous API or web image is
  always safe. An ingestion writer that changed what it writes cannot simply return to older
  code against newer data. A schema operation cannot be undone by swapping images.
- Some services must never be restarted as a side effect of a release: ClickHouse, and the
  IB Gateways, which hold a logged-in session.
- Release automation reaches the VPS through SSH and sudo. That grant should be two fixed
  commands whose arguments are validated, not a shell.

## Decision

- **One host deployer.** [`deploy/host/factorlab_deploy.py`](../../deploy/host/factorlab_deploy.py)
  is stdlib Python, runs as root, and is reached only through two sudoers entry points,
  `deploy-component.sh` and `deploy-platform.sh`. Its commands are `deploy`, `rollback`,
  `platform`, `seed`, `status` and `compose`.
- **Deploy, in order.**
  1. *Validate.* The name, the SemVer version, and an image of exactly
     `ghcr.io/arjundoshi221/factorlab-<name>@sha256:<digest>`. The bundle holds only plain
     files with no path escapes, and its `component.json` must match. A platform release must
     be installed, and a recorded version is redeployed only through `rollback --to`.
  2. *Lock.* Take the host-wide release lock, which the legacy script shares.
  3. *Preflight and policy lint.* Render the merged Compose model with the new fragment and
     pin. Refuse a service that runs any other image, sets `privileged`, `network_mode`,
     `pid`, `ipc`, `cap_add` or `container_name`, publishes a port beyond `127.0.0.1`,
     bind-mounts outside `/var/log/factorlab/`, `/var/lib/factorlab/app-data`,
     `/var/lib/factorlab/docker-images` or `/etc/factorlab/`, or has no `logging` block.
     Then pull the image and check its component and version labels.
  4. *Record.* Save the bundle, previous pin, manifest and fragment in `releases/<name>/<v>/`.
  5. *Activate.* Atomically replace the component's fragment, its `component.json` and its
     pin in `state/images.env`, then fix log and data directory ownership.
  6. *Roll.* Recreate only that component's daemons (`up -d --no-deps --force-recreate`),
     run its `run-on-deploy` jobs, and leave `scheduled` services to their host timers.
  7. *Verify.* Every daemon must run the pinned image, become healthy within 180 seconds,
     and stay up with no restarts through a stabilization period.
- **Rollback classes** (the manifest's `platform.rollback`), applied when a step after
  activation fails:
  - `auto` (api, web, secrets-agent): restore the previous fragment and pin, recreate, verify.
  - `writer` (the ingest components): the same when `data_contract` is unchanged. If it
    changed, stop the component's services and report `fix-forward-required`.
  - `forward-only` (schema-migrator): report the failure and roll nothing back.
  - With no previous release to return to, the services are stopped.
- **Platform releases recreate nothing.** `platform` installs `deploy/`, keeps
  `production.env`, never overwrites a component's fragment, runs `prepare-host.sh` and checks
  the model. On failure it restores the previous `deploy/`. Services whose running config hash
  differs from the model are written to `releases/platform/<v>/drift` for the next component
  release or a maintenance window. The first platform release seeds every component on the
  monolith image digest (`seed`), so nothing restarts during the bridge.
- **Compose split.** [`deploy/compose.base.yml`](../../deploy/compose.base.yml) (platform-owned)
  holds the networks, secret tmpfs volumes, ClickHouse, the optional IB Gateways and the ops
  tools. Each `components/<name>/deploy/compose.yaml` holds that component's services. The
  deployer merges them on the host with `images.env` and profiles. `compose.production.yml` is
  generated from the same sources only for the legacy path, and once `state/images.env`
  exists, `deploy-release.sh` and `rollback-release.sh` refuse to run.
- **Schema changes are forward-only.** ClickHouse v2 waves are never edited or deleted once
  applied. [`tools/schema_checksums.py`](../../tools/schema_checksums.py) keeps
  `libs/schema/src/factorlab/schema/sql/clickhouse/v2/checksums.lock` (checksum, `applied` or
  `pending`, id), checked in pre-commit and CI. The migration runner also refuses checksum
  drift against its journal. The schema-migrator's deploy job (`factorlab-db bootstrap`)
  applies no DDL. Production waves are a separate, reviewed operation.

## Consequences

- A component release touches only that component, and a failed one returns it to the
  previous state automatically when that is safe.
- Writers stop instead of flapping between incompatible versions. Bumping `data_contract`
  is how a writer declares a change to what it writes. A missed bump weakens that guard.
- Every deploy, rollback and outcome is on disk (`releases/<name>/history.log`), and
  `factorlab_deploy.py status` shows the version and digest per component.
- Drift is reported, not repaired. Someone has to act on `releases/platform/<v>/drift`.
- The deployer is security-critical root code. It is covered by `tests/deploy/test_deployer.py`
  and `tests/test_deploy_host.py`. It runs on the host's system Python (targeting 3.10) and
  has no third-party dependencies.

## Alternatives considered

- **Keep `deploy-release.sh` and add a component argument.** Rejected: its model is one
  image and one compose file, and per-component pins, fragments and recovery do not fit it.
- **Kubernetes, Nomad or Swarm.** Rejected: an orchestrator is far more machinery than one
  VPS needs, and it would not remove the need for rollback classes.
- **Always roll back automatically.** Rejected: rolling back a writer across a changed data
  contract, or a schema operation, can corrupt or mislabel data.
- **Recreate drifted services during a platform release.** Rejected: it would restart
  ClickHouse and log out the IB Gateways as a side effect.
- **Run migrations on every schema-migrator release.** Rejected: production schema changes
  need review and the owner's explicit request.
