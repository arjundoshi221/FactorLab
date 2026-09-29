# ingest-broker

> Read-only IBKR account mirror: scheduled Gateway snapshots into `broker.*`.

## Purpose

`ingest-broker` takes snapshots of IBKR paper and live accounts (positions, account
state, executions, open orders) from read-only IB Gateways and writes them to ClickHouse
`broker.*`, archiving every Gateway response to `raw.archive`. It is an optional writer
component (image `ghcr.io/arjundoshi221/factorlab-ingest-broker`, manifest
[component.yaml](component.yaml)), off unless the `ibkr` compose profile is enabled.

## Owns and does not own

- Owns: the `factorlab-ingest-broker` CLI ([cli.py](src/factorlab/components/ingest_broker/cli.py)),
  the snapshot runner and daemon loop ([snapshot.py](src/factorlab/components/ingest_broker/snapshot.py)),
  and the providers the image ships ([providers.py](src/factorlab/components/ingest_broker/providers.py): `ibkr`).
- Does not own: the IB Gateways (the operator's machine over Tailscale by default, or the
  platform's optional `ibkr-gateway-paper` / `ibkr-gateway-live` services in
  [compose.base.yml](../../deploy/compose.base.yml)), the IBKR provider and normalization
  (`providers/ibkr`, `factorlab.sources.ibkr`), the writers
  ([`V2BrokerStorage`](../../libs/storage/src/factorlab/storage/v2_broker.py)), Gateway
  login secrets (`secrets-agent`, Cloudflare Secrets Store).

## Entry points

- Console script `factorlab-ingest-broker`:
  - `snapshot [--once | --daemon] [--modes paper,live] [--at HH:MM,...] [--run-on-start]
    [--client-id N] [--executions-lookback-hours 24] [--dry-run] [--log-level INFO]`.
    `--once` is the default; `--dry-run` connects and normalizes but writes nothing.
  - `engine`: the provider-agnostic engine (validate, run, daemon, replay).
- Compose service `ibkr-snapshot` ([deploy/compose.yaml](deploy/compose.yaml)), profile
  `ibkr`: `snapshot --daemon --run-on-start --modes ${IBKR_SNAPSHOT_MODES:-paper,live}`,
  `mem_limit: 384m`, `stop_grace_period: 60s`, network `backend`, no ports.
- The process runs under `factorlab.runtime.supervised` crash supervision (name
  `ibkr_broker_snapshot`).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Enable | `FACTORLAB_IBKR_ENABLED` (`production.env`) | off | turns on the `ibkr` profile |
| Modes | `IBKR_SNAPSHOT_MODES` | `paper,live` | |
| Gateways | `IBKR_HOST_PAPER`, `IBKR_PORT_PAPER`, `IBKR_HOST_LIVE`, `IBKR_PORT_LIVE` | `ibkr-gateway-paper:4004`, `ibkr-gateway-live:4003` | Tailscale IP and 4002/4001 for a laptop Gateway |
| Schedule | `IBKR_SNAPSHOT_TIMES` | `06:00,16:30` | America/New_York, XNYS sessions only |
| Lock | `IBKR_SNAPSHOT_LOCK` | compose: `/tmp/ibkr-snapshot.lock` | code default `<home>/data/ibkr-snapshot.lock` |
| Client id | `IBKR_CLIENT_ID` (declared secret) / `--client-id` | `2` (`SNAPSHOT_CLIENT_ID`) | the CLI passes `--client-id`'s default explicitly |
| ClickHouse | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USERNAME`; secret `CLICKHOUSE_PASSWORD` | compose: `clickhouse`, `8123`, `default`, `factorlab` | `ibkr_client_runtime` tmpfs at `/run/secrets/app` |

No IBKR credentials reach this container; Gateway logins go only to the Gateway volumes.

## Data

- Writes `broker.positions_snapshot`, `broker.account_state_snapshot`, `broker.executions`,
  `broker.open_orders_snapshot`, `raw.archive` (`transport='tcp_socket'`), `ref.*` identity
  rows, `meta.unresolved_entities`, `meta.source_status` (`source` = `ibkr`) and
  `meta.ingestion_runs`. Reads `ref.broker_metrics_map` and `ref.execution_methods`.
- Pipeline (frozen) `ibkr_broker_snapshot`, `source` = `ibkr`, `universe` = `ibkr_accounts`;
  `source_channel` = `paper_gateway` / `live_gateway`.

## Dependencies and contracts

- Workspace: `factorlab-core`, `-runtime`, `-calendars`, `-ingest`, `-storage`,
  `-orchestration`, `factorlab-provider-ibkr` ([pyproject.toml](pyproject.toml)); `ib-async`
  may ship only in this image ([tools/verify_image.py](../../tools/verify_image.py)).
- Contracts: the shared provider contract (`factorlab.ingest.provider.run_provider`): one
  `meta.ingestion_runs` row per snapshot, units per mode and dataset; `data_contract: 1`.
- `meta.source_status` for `ibkr`: `ready`, `incomplete` (partial), `error`, `stopped`,
  `waiting` (between slots). Non-success runs also send a notification.

## Observability

- Logs: `/var/log/factorlab/ingest-broker/ibkr-snapshot.jsonl`:
  `uv run python tools/read_logs.py tail --component ingest-broker`.
- Heartbeat `ibkr_broker_snapshot` (file under `FACTORLAB_HEARTBEAT_ROOT`, default
  `/app/data/_heartbeat`), ticked every 30 s while waiting for a slot and before each run. The image `HEALTHCHECK` runs
  `factorlab-healthcheck heartbeat ibkr_broker_snapshot --max-age 300`; the deployer waits
  for Docker `healthy`.
- Setup and troubleshooting: [docs/operations/ibkr-gateway-setup.md](../../docs/operations/ibkr-gateway-setup.md).

## Tests

[tests/](tests/) (storage and snapshot script; fixtures from `factorlab.testkit.ibkr` in
[conftest.py](tests/conftest.py)):

```bash
uv run pytest components/ingest-broker
```

## Release and rollback

`.\deploy\release.ps1 -Component ingest-broker [-Bump ...] [-DryRun]` -> tag
`ingest-broker/vX.Y.Z` -> [component-release.yml](../../.github/workflows/component-release.yml)
-> `deploy-component.sh ingest-broker ...` via [_deploy.yml](../../.github/workflows/_deploy.yml).
Rollback class `writer`, `data_contract: 1` ([factorlab_deploy.py](../../deploy/host/factorlab_deploy.py)).
The legacy monolith path (`release/*`, `deploy-release.sh --profile ibkr`) still runs
production until the rollout completes.

## Pitfalls

- With `FACTORLAB_IBKR_ENABLED` off the service is absent from the model; the manifest marks
  it profile-gated, so a deploy updates the pin and verifies nothing.
- Releases never recreate the Gateways; they keep their logged-in sessions. A Gateway that is
  logged out or awaiting 2FA fails only its own mode's units (run `partial`).
- The heartbeat does not tick during a snapshot; a run that outlasts the 300 s max age
  (plus three healthcheck retries) turns the container `unhealthy` (and fails a deploy's verification if it happens then).
- The Gateways must run with the read-only API (`READ_ONLY_API: "yes"` in the base); this
  component must never place orders.
- [`component-rollback.yml`](../../.github/workflows/component-rollback.yml) does not check
  `data_contract`; never roll back across a contract bump.
