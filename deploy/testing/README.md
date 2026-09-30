# Production ingestion tests from a branch

This is an owner-authorized exception for testing ingestion component images on
the existing VPS before merging the restructure. The normal release helper and
its `main` policy are unchanged. Do not start a production test without an
explicit request to replace the live ingestion services.

The 2026-09-30 test lives on `testing/prod-ingestion-20260930`. Images were built
from tracked source commit `d59940263ca008d5ac09462eb1e36a039236e7d6`, using the
component Dockerfiles and frozen workspace lock. No `.env` or untracked files
were included in the source archive. `main` was not changed.

## Host layout

The private, root-owned test directory is
`/opt/factorlab/deployment-tests/20260930-d599402/`. It contains:

- `source/`: the exported source tree.
- `backup/deploy/` and `backup/political-cron`: the preceding host configuration.
- `rollback.compose.json`: a resolved model pinned to the previously running images.
- `candidate.compose.json`: the proposed model with new ingestion image IDs.
- `before-containers.json`, `protected-containers.json`, `after-containers.json`:
  container IDs, image IDs, start times and restart counts, without environments.
- `ingestion_test.py`: the installed copy of this directory's runner.

Compose models and configuration backups can contain sensitive settings. Keep
them root-only on the host; never commit or download them into reports.

## Preparation and execution

First archive tracked source with `git archive` and transfer it to the host.
Create a new private test directory; copy the current deployment directory and
political cron into its `backup/`; extract source into `source/`. Build each of
`ingest-india`, `ingest-us`, `ingest-broker`, `ingest-political` from its component
Dockerfile, tagging it `factorlab-test-<component>:<test-id>` and setting the
`VERSION` and `COMMIT` build arguments. Keep preceding images available.

Copy the runner to the test directory and run as root:

```bash
sudo python3 /opt/factorlab/deployment-tests/20260930-d599402/ingestion_test.py plan 20260930-d599402
sudo python3 /opt/factorlab/deployment-tests/20260930-d599402/ingestion_test.py apply 20260930-d599402
sudo python3 /opt/factorlab/deployment-tests/20260930-d599402/ingestion_test.py verify 20260930-d599402
```

`plan` resolves the current and proposed Compose models, checks component labels
and production command imports, and records the exact running images. `apply`
takes the shared release lock, stops existing writers before replacing them,
installs the resolved model at `/opt/factorlab/deploy/compose.production.yml`,
and recreates ingestion daemons using `--no-deps`. It checks startup stability
and unchanged API, ClickHouse and secret-agent container identities. A startup
failure restores the saved model and preceding ingestion images.

The installed resolved model also makes the existing political cron use the new
political image. Do not run a normal release concurrently with this test.

## Compatibility and verification limits

The currently installed secret agent renders files as root-owned `0400`; the
new component images declare user `10001`. The test runner uses a `0:0` user
override to consume existing secret volumes and runtime data without changing
their permissions. This validates the component images' runtime behavior with
the current host, but does not validate the eventual non-root rollout or the
per-component host deployer.

Check new `meta.ingestion_runs` records and raw-to-curated lineage, provider
status, configured universe, logs and restarts. India needs an open-session
check, or a controlled `--once` sweep with its daemon stopped. Political runs
must use `/run/lock/factorlab-political.lock` to avoid overlapping cron.
Broker ingestion requires a reachable, logged-in read-only Gateway.

To restore the preceding ingestion images and model:

```bash
sudo python3 /opt/factorlab/deployment-tests/20260930-d599402/ingestion_test.py restore 20260930-d599402
```

This restores deployment configuration and code, not database contents. No
schema migrations or data deletion are part of this workflow. Collector code
under component `legacy/` directories remains in use and cannot yet be removed.

## Remaining components and non-root operation

On September 30 the owner also requested deployment of the remaining components.
Build `secrets-agent`, `schema-migrator`, `api`, and `web` from the same archived
source and install [component_test.py](component_test.py) beside the ingestion
runner. It supports `apply <test-id>` and `verify <test-id>`.

The runner saves `remaining-before.json`, runtime ownership metadata, and primary
API/ClickHouse identities. It stops writers during the secret renderer transition,
hands runtime data and logs to their image UIDs, installs the agent, checks schema
readiness without migrations, and starts collectors as UID 10001. It runs web at
`127.0.0.1:8080` and `api-candidate` at `127.0.0.1:18000`. The primary API stays on
8000 to serve the existing hub until Cloudflare Tunnel/Access routing is ready.

`rollback-check <test-id>` tests restoration on these private ports: the API
candidate returns to the preceding monolith image and web returns to a separately
built previous test version (`factorlab-test-web:20260930-previous`, build argument
`VERSION=0.0.0-test.previous`). It then restores both current images and rechecks
production reads. This is a live image/configuration restoration check, not a
test of registry-backed component release history or the Actions rollback workflow.

Host tools and timers can be installed before platform image-pin bootstrap.
`factorlab-compose` uses the existing live Compose model until `state/images.env`
exists. Keep that bootstrap pending while the public API still needs its old UI
and test images are local, unpublished image IDs. The political cron continues
using the live model, with its output moved to the managed log directory.

The earlier ingestion-only restore runner is for the original root trial. After
the remaining rollout, use the private `remaining-before.json` and ownership
record when restoring this stage; do not use the old ingestion-only `verify`
as proof of secret-agent identity, because the agent has intentionally changed.
