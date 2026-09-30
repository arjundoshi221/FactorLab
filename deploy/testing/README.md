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
