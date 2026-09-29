---
id: ADR-0013
title: One image per component, released on its own tag
status: accepted
date: 2026-09-29
status_note: Built on restructure/platform-v3, but no unit has been released on its own tag yet; production still runs the monolith, and the rollout is feature F-007.
status_confirmed: false
---
# ADR-0013 — One image per component, released on its own tag

## Context

- Production ran one image (`ghcr.io/arjundoshi221/factorlab`) for every service. It was
  released by `release/<UTC>-<sha>` tags through `release.yml` and `deploy-release.sh`. A
  change to the web UI rebuilt and redeployed the ingestion writers too, and a bad release
  could only be rolled back as a whole.
- The workspace ([ADR-0012](0012-uv-workspace-monorepo.md)) gives each component its own
  dependency closure. This decision turns those closures into images that are built,
  verified, versioned and deployed on their own.

## Decision

- **One image per component.** Each `components/<name>/Dockerfile` builds
  `ghcr.io/arjundoshi221/factorlab-<name>`. A Python image installs only its package's
  closure from `uv.lock` (`uv sync --frozen --no-dev --no-editable --package <pkg>`), runs as
  UID 10001, sets `FACTORLAB_HOME=/app`, and carries the labels `io.factorlab.component` and
  `org.opencontainers.image.version`. `web` is a static (nginx) image.
- **A manifest per component.** `components/<name>/component.yaml` declares the package,
  console entry point, image, Dockerfile, the providers it ships, the secret *names* it reads,
  its compose services (`daemon`, `run-on-deploy` or `scheduled`), its health check, and a
  `platform` block (`image_var`, rollback class, `data_contract`, user, log directory).
  [`tools/components.py check`](../../tools/components.py) validates every manifest against the
  code: the directory name, the image name, `image_var`, the Dockerfile and its
  `.dockerignore`, the compose fragment policy, the version format, that each service has one
  owner, that the entry point is a `[project.scripts]` entry, that the Dockerfile installs
  that package, and that the providers match both the `factorlab-provider-*` dependencies and
  the component's `PROVIDERS` tuple.
- **Verify the closure.** [`tools/verify_image.py`](../../tools/verify_image.py) checks a
  published image. Its installed distributions must equal `uv export --package <pkg>` exactly.
  Heavy dependencies may appear only where they belong: `ib-async` only in ingest-broker,
  `pdfplumber` only in ingest-political, `fastapi` and `uvicorn` only in api, and `pytest`
  nowhere. The user and labels must match, and `<entrypoint> --help` must exit 0.
- **Tags.** Each release unit is tagged `<unit>/vX.Y.Z`. A unit is a component, `platform`
  (`deploy/`), or a Cloudflare Worker (`cloudflare/<name>`). Versions live with the code:
  `[project].version` in `pyproject.toml` for Python components, `package.json` for web and
  the Workers, and `deploy/component.yaml` for the platform. Every unit keeps a `CHANGELOG.md`.
- **Cutting a release.** `deploy/release.ps1 -Component <unit> [-Bump patch|minor|major]
  [-DryRun]` runs [`tools/release.py prepare`](../../tools/release.py) on a clean, synchronized
  main. `prepare` refuses a version that is already tagged. It also refuses when nothing in
  the unit's closure (`tools/affected.py`) changed since its last tag. Otherwise it writes the
  version, prepends a CHANGELOG section built from conventional commits, and runs `uv lock`
  for Python units. The script then pushes the release commit to main and an annotated tag.
- **The pipeline.** [`component-release.yml`](../../.github/workflows/component-release.yml)
  validates the tag name, requires the tagged commit to be on main, and runs
  `tools/release.py check-tag`. It then tests the whole workspace (or runs npm for `web`) and
  calls reusable workflows. `_release-image.yml` builds, publishes (with provenance and SBOM),
  attests and verifies the image. It never overwrites a published version; a manual re-run
  reuses the digest. `_deploy.yml` ships the deploy bundle and runs the host deployer
  ([ADR-0015](0015-host-deployer-and-rollback-classes.md)) with the image pinned by digest.
  `_release-worker.yml` validates and builds a Worker, whose deploy stays manual.
  `component-rollback.yml` returns a component to a recorded version.
- **The legacy path stays for now.** `release.ps1` without `-Component` still pushes a
  `release/*` tag to `release.yml`, which builds the root monolith `Dockerfile`. Until a
  component ships its own image, its compose fragment falls back to `FACTORLAB_IMAGE`. Both
  paths exist until the monolith is retired (rollout R4).

## Consequences

- A release rebuilds and restarts only the unit whose closure changed. A library change
  makes every component that depends on it releasable, and `tools/affected.py` says which.
- An image cannot quietly ship a dependency it does not declare, or one that belongs to
  another component. Image size and attack surface follow the component, not the monolith.
- Versions and changelogs are per unit, so the question "what is running?" is answered per
  component (`factorlab_deploy.py status`).
- While both paths coexist there are two ways to release. Once the host has
  `state/images.env`, the legacy `deploy-release.sh` and `rollback-release.sh` refuse to run,
  so the two cannot fight over the same services.
- More moving parts: eleven CHANGELOGs (eight components, the platform and two Workers),
  eight manifests and eight Dockerfiles to keep consistent. The manifest check and
  `render-compose --check` run in pre-commit and CI.
- No CHANGELOG has a release section yet. The first per-unit releases are steps R2 and R3
  of F-007.

## Alternatives considered

- **Keep the monolith image.** Rejected: no isolation between services, and every change is
  a full-stack release.
- **One image with a different entry point per service.** Rejected: it keeps every
  dependency in every container and cannot prove a closure.
- **Lockstep versions (one version for all units).** Rejected: most releases touch one or
  two units, and lockstep versions would bump and redeploy units that did not change.
- **Moving tags (`latest`, `stable`) instead of immutable versions.** Rejected: the deployer
  pins digests, and rollback needs a stable name for every previous release.
- **Release from CI on every merge to main.** Rejected for now: releases are deliberate,
  owner-initiated steps, and writers are released outside market hours.
