# VPS restructure validation — 2026-09-30

Read-only VPS checks and local deployment checks were requested to establish whether
the restructure can run in production before legacy code is removed.

## Result

The initial read-only check did not deploy the restructure. Later on September 30,
the owner explicitly authorized replacing production ingestion services from a
separate test branch while preserving `main`. The ingestion component test was
applied at approximately 10:56 UTC; see the live result below.

Local checkout: clean `restructure/platform-v3`, commit `d599402`. Remote `main`
remained at `542950b` when checked. Releases require clean, synchronized `main`.

## Observed VPS state

- Current release: `20260925T163007Z-8f72e1a817df`.
- API, India, US, US universe, IBKR snapshot and secrets agent use the monolith
  image ID prefix `4a5b239e5a96`. They were running; secrets agent was healthy.
- ClickHouse 25.3 was running and healthy.
- Local `/health` and `/hub/api/v1/overview` both returned HTTP 200.
- `/opt/factorlab/state/images.env` and the new host deployer were absent.
- Root disk: 23 GB available; data disk: 37 GB available.
- Caddy was active and listening publicly on 80/443. API and ClickHouse ports
  were bound to loopback.
- Neither `factorlab-cloudflared` nor `cloudflared` was active. The expected
  `/etc/factorlab/identity/cloudflared.env` token file was absent.
- An unauthenticated external request to
  `https://arjundoshi221.com/hub/api/v1/overview` returned HTTP 200 without an
  Access redirect. This fails the documented prerequisite for rollout.

## Local verification

Python 3.12.14 from the workspace virtual environment:

- `tests/deploy/test_bundles.py` and `tests/test_deploy_host.py`: **27 passed**.
- `tests/deploy/test_deployer.py`, `test_log_reader.py`, `test_prune_images.py`
  and `tests/architecture`: **62 passed**.

Tests used a scratch directory under `.tmp/vps-validation` and Git Bash first
on PATH. The initial attempts failed because the default pytest temp directory
was inaccessible and Windows selected its WSL Bash launcher. These results are
local tests, not evidence of production compatibility or fresh ingestion.

## Remaining live gates

These gates describe the eventual normal release rollout. The owner-authorized
ingestion test below bypasses merging and platform bootstrap for this test only.

Follow [rollout.md](rollout.md) in order:

1. Configure the hub tunnel and whole-hostname Cloudflare Access; install its
   root-only token using the documented process. Verify Access redirects and
   removal of public web listeners.
2. Confirm green CI and merge the restructure with a merge commit. Confirm CI
   on `main`, then run the edge canary.
3. Use `deploy/release.ps1` for the initial monolith release from the new layout,
   outside market hours. Verify workflow outcome, health, hub reads, ingestion
   runs and fresh raw-to-curated writes.
4. Bootstrap platform deployment and release components in the runbook order.
   Validate tunnel routing before releasing the API image without the UI.
5. Exercise API and web rollback and observe the writer services through their
   scheduled collection windows before retiring monolith deployment machinery.

## Legacy removal limit

The component CLIs still call production collector implementations under
`components/ingest-india`, `ingest-us` and `ingest-political` `legacy/` directories,
as documented in their `CONTEXT.md` files. Component image rollout does not
establish that those implementations can be deleted. Their engine replacements
need separate cutover and data-continuity validation. Do not delete legacy
collector code based only on container health or this local test result.

## Live ingestion component test

- Branch: `testing/prod-ingestion-20260930`, based on `d599402`.
- Built all four ingestion component images on the VPS from tracked source and
  the frozen uv lock. Production subcommand import checks passed for all four.
- Replaced `ingest-india`, `ingest-us`, `universe-us`, and `ibkr-snapshot`; changed
  the political job's image in the live model. Saved the preceding model and
  pinned image IDs under `/opt/factorlab/deployment-tests/20260930-d599402/`.
- API, ClickHouse and secrets-agent container IDs and start times remained
  unchanged. API health and overview passed; new ingestion daemons had zero
  restarts during initial verification.
- US universe sync: success, 503 stocks. US daily recovery wrote new data;
  a lineage check found 60,795 daily bars across eight runs with matching raw
  archive and ingestion-run references after activation.
- US recovery reported partial coverage with missing historical bars. Before
  activation, the previous two days already had 514 partial recovery runs; this
  condition was not introduced by the component image switch.
- Political bounded test (`--ptr-limit 1`): success, five work units, zero failed,
  5,068 rows recorded. One parsed trade had matching raw and run references.
- IBKR container was healthy, but its initial snapshot failed because the laptop
  Gateway connection timed out. A failed broker run existed at 10:00 UTC before
  activation. End-to-end broker writes remain unverified.
- India started after NSE close. A controlled one-shot sweep, with its daemon
  stopped to avoid overlapping writers, completed successfully at 11:09:14 UTC:
  2,892 requested series, 2,892 successful, zero failed, 87,327 rows written.
  Lineage checks found 5,333 equity bars and 81,994 futures bars with matching
  raw archive and ingestion-run references. Upstox authentication passed.
  The one-shot process exited zero and the new India daemon resumed successfully.
  Initial reference synchronization took approximately six minutes; the next
  open-session scheduled sweep remains an observation gate.

The test uses a root user override because the existing agent renders `0400`
root-owned secret files. Non-root compatibility and the per-component host
deployer are not validated by this test. No production migrations, data deletion,
release tags or changes to `main` were performed. See
[the test runbook](../../deploy/testing/README.md) for restore commands.
