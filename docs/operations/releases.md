# Releases

Every release unit ships on its own immutable tag `<unit>/vX.Y.Z`: the eight components,
`platform` (deploy/) and the two Cloudflare Workers. Decision records:
[ADR-0013](../decisions/0013-per-component-images-and-tags.md) and
[ADR-0015](../decisions/0015-host-deployer-and-rollback-classes.md). Operator detail for the
VPS side is in [deploy/README.md](../../deploy/README.md#per-component-releases).

A release deploys to production. Start one only when you intend to change production.

## Cutting a release

From a clean `main` equal to `origin/main`, with a green `ci.yml` for HEAD:

```powershell
.\deploy\release.ps1 -Component ingest-us -Bump minor -DryRun   # version + changelog preview
.\deploy\release.ps1 -Component ingest-us -Bump minor
```

`tools/release.py prepare` refuses a version that is already tagged and a release whose
closure did not change since the unit's last tag (`uv run python tools/affected.py closure
<unit>` shows what counts: the unit's directory, its workspace dependencies, configs it
copies, and its locked third-party packages). It writes the version (`pyproject.toml`,
`package.json` or `deploy/component.yaml`), prepends a `CHANGELOG.md` section from
conventional commits, commits `chore(release): <unit> vX.Y.Z` and pushes the annotated tag.

**Version meaning.** Major: an incompatible change to what the unit exposes or writes
(API response shape, stored row meaning, a required new secret or host step). Minor:
new capability. Patch: fixes. A writer's `data_contract` (component.yaml) is separate and
must be bumped whenever the data it writes changes shape or meaning.

## What the pipeline does

`.github/workflows/component-release.yml`:

1. **authorize** — the tag names a unit at its declared version and the commit is on `main`;
2. **test** — the Python suite (or web tests and build);
3. **image** (`_release-image.yml`) — build, publish `<image>:<version>` (never overwritten),
   provenance + SBOM + attestation, then verify the pulled image's labels and dependency
   closure against `uv.lock`;
4. **deploy** (`_deploy.yml`, environment `production`) — upload the bundle and run
   `sudo /opt/factorlab/deploy/scripts/deploy-component.sh <unit> <version> <image@digest> <bundle>`
   (platform: `deploy-platform.sh`); workers are validated and bundled, then deployed by hand
   with `npx wrangler deploy`.

The host deployer validates before touching anything (digest, bundle, fragment policy,
image labels), records the current state, swaps only this unit's fragment and image pin,
recreates only its services and verifies them (image id, health, stability).

## When it fails: rollback classes

| Class (`component.yaml`) | On a failed deploy | Manual rollback (`component-rollback.yml`) |
|---|---|---|
| `auto` (api, web, secrets-agent) | previous release restored and verified | allowed |
| `writer` (ingest-*) | restored if `data_contract` is unchanged; otherwise the component is stopped and reported `fix-forward-required` | refused across a `data_contract` change |
| `forward-only` (schema-migrator) | reported failed; nothing rolled back | refused |

Fix forward with a new patch release. To re-run a tag whose transfer failed, dispatch
`component-release.yml` with the tag (reuses the published image). A platform release
installs `deploy/`, keeps `production.env`, runs `prepare-host.sh` and recreates nothing.

## Checking a release

- The workflow's job summary: verification and rollback result.
- `uv run python tools/read_logs.py list` — the new version per component; `errors --since 1h`.
- Writers: fresh `meta.ingestion_runs` rows (`factorlab-clickhouse` skill).
- The hub's Docker images page: "Running components" (deployed version per component).

## The legacy monolith path

Until the rollout completes ([rollout.md](rollout.md)), production runs one image for every
service: `.\deploy\release.ps1` without `-Component` pushes `release/<UTC>-<sha>`, which
`.github/workflows/release.yml` deploys with `deploy-release.sh`. Once the platform bootstrap
has written `/opt/factorlab/state/images.env`, the legacy scripts refuse to run.

Schema migrations are never part of a release: they are separate, reviewed, forward-only
operations (`factorlab-db migrate apply`, then `tools/schema_checksums.py applied <id>`).
