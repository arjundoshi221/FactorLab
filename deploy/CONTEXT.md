# platform

> Compose base, host deployer and scripts, edge tunnel unit, and host log rotation and retention for the FactorLab VPS.

## Purpose

`deploy/` is the `platform` release unit ([component.yaml](component.yaml)). It owns what no
single component does: the Compose base (networks, tmpfs secret volumes, ClickHouse, the
optional IB Gateways, ops tools), the root-run host deployer that installs component
releases, host provisioning, log rotation and the restricted log-reader account. It ships
no image; a platform release installs files on the host and recreates no services. The
operator runbook is [README.md](README.md).

## Owns and does not own

- Owns: [compose.base.yml](compose.base.yml); [host/factorlab_deploy.py](host/factorlab_deploy.py)
  and [host/factorlab_log_reader.py](host/factorlab_log_reader.py); [scripts/](scripts/)
  (`deploy-component.sh`, `deploy-platform.sh`, `factorlab-compose`, `prepare-host.sh`,
  `install-edge-tunnel.sh`, `install-political-cron.sh`, `run-political-ingest.sh`,
  `collect-docker-images.py`, `prune-images.py`, legacy `deploy-release.sh` /
  `rollback-release.sh`); [logrotate/factorlab](logrotate/factorlab); [systemd/](systemd/);
  [ssh/60-factorlab-logs.conf](ssh/60-factorlab-logs.conf); [cron/](cron/); [clickhouse/](clickhouse/)
  config; [us-universe.yaml](us-universe.yaml) (seed only); [release.ps1](release.ps1).
- Does not own: each component's fragment `components/<name>/deploy/compose.yaml` and
  manifest; [compose.production.yml](compose.production.yml) is generated from the base plus
  fragments (`uv run python tools/components.py render-compose`), never edited; the tunnel
  ingress and Access application (Cloudflare dashboard, [edge/README.md](edge/README.md)).

## Entry points

- Host deployer (stdlib Python, root): `factorlab_deploy.py deploy <component> <version>
  <image@sha256> <bundle.tgz>`, `rollback <component> [--to <version>]`,
  `platform <version> <bundle.tgz>`, `seed <platform-bundle-dir> <image@sha256>` (monolith
  bridge), `status`, `compose -- <args>`. Last output lines are `FACTORLAB_RESULT_*`.
- Sudoers entry points: `/opt/factorlab/deploy/scripts/deploy-component.sh` (`<c> <v> <image>
  <bundle>` or `rollback <c> [<v>]`) and `/opt/factorlab/deploy/scripts/deploy-platform.sh
  <v> <bundle>`. Operator: `sudo factorlab-compose <args>` (compose over the live model).
- Compose project `factorlab`, services in the base: `clickhouse` (`127.0.0.1:8123`,
  `127.0.0.1:9000`), `ibkr-gateway-paper` / `ibkr-gateway-live` (profile `ibkr-gateway`,
  VNC on `127.0.0.1:5900` / `5901`), `uptime-kuma` (profile `ops`, `127.0.0.1:3001`),
  `portainer` (profile `admin`, `127.0.0.1:9443`). Networks `backend`, `edge`.
- systemd: `factorlab-cloudflared.service`, `factorlab-docker-images.timer` (every minute),
  `factorlab-image-prune.timer` (03:30 UTC), `logrotate.timer` drop-in (hourly).
- Log reader: `/usr/local/bin/factorlab-log-reader` (`list`, `tail`, `errors`, `run`), the
  ForceCommand for SSH user `factorlab-logs`; client [tools/read_logs.py](../tools/read_logs.py).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Host root | `FACTORLAB_HOST_ROOT` | `/opt/factorlab` | deployer |
| Release lock | `FACTORLAB_RELEASE_LOCK` | `/var/lock/factorlab-release.lock` | shared with `deploy-release.sh`; 1200 s wait |
| Stabilization | `FACTORLAB_STABILIZATION_SECONDS` | `20` | health timeout is 180 s |
| Profiles | `FACTORLAB_IBKR_ENABLED`, `FACTORLAB_IBKR_VPS_GATEWAYS`, `FACTORLAB_WEB_ENABLED` | off | in `production.env`; map to `ibkr`, `ibkr-gateway`, `web`; `jobs` is always on |
| Image pins | `FACTORLAB_<NAME>_IMAGE` | - | `/opt/factorlab/state/images.env`, deployer-owned |
| Base images | `CLICKHOUSE_IMAGE`, `IBKR_GATEWAY_IMAGE`, `UPTIME_KUMA_IMAGE`, `PORTAINER_IMAGE` | see base | |
| CI secrets | `VPS_HOST`, `VPS_USER`, `VPS_SSH_PRIVATE_KEY`, `VPS_SSH_HOST_KEY` | - | GitHub `production` environment |
| Tunnel token | `TUNNEL_TOKEN` in `/etc/factorlab/identity/cloudflared.env` | - | root-only |
| Log client | `FACTORLAB_LOGS_SSH_HOST`, `_PORT`, `_USER`, `_KEY_PATH` | `CLICKHOUSE_SSH_HOST`, 22, `factorlab-logs` | workstation |

`production.env` holds operator settings and is preserved across platform releases.

## Data

Host paths: `/opt/factorlab/{deploy,components/<name>,state/images.env,releases/<name>/<v>/}`
(bundle, `previous.json`, `result`, `activated-at`, `history.log`; platform: `drift`,
`previous-deploy`), `/var/lib/factorlab/clickhouse`, `/mnt/factorlab-data/clickhouse-raw`
(must be mounted, else `prepare-host.sh` refuses), `/var/lib/factorlab/app-data`,
`/var/lib/factorlab/docker-images/snapshot.json`, `/etc/factorlab/identity`,
`/etc/factorlab/us-universe.yaml`, `/etc/factorlab/log-reader/authorized_keys`,
`/var/log/factorlab/<component>/` (`<uid>:factorlab-logs`, 2750).

## Dependencies and contracts

- Host: Docker with Compose v2, `/usr/bin/python3` (3.10+), cron, `flock`, `cloudflared`,
  OpenSSH with `sshd_config.d`.
- Fragment policy enforced by `lint_model`: every service uses the release image, no
  `privileged` / `network_mode` / `pid` / `ipc` / `cap_add` / `container_name`, ports only on
  `127.0.0.1`, bind mounts only under `/var/log/factorlab/`, `/var/lib/factorlab/app-data`,
  `/var/lib/factorlab/docker-images`, `/etc/factorlab/`, and a `logging` block.
- Image checks: `io.factorlab.component` and `org.opencontainers.image.version` labels must
  match; image must be `ghcr.io/arjundoshi221/factorlab-<name>@sha256:...`.
- Bundles from `uv run python tools/components.py bundle <name|platform> <out.tgz>`.

## Observability

- Deploy outcome: GitHub job summary and `FACTORLAB_RESULT_*`; on the host `sudo python3
  /opt/factorlab/deploy/host/factorlab_deploy.py status` and `releases/<name>/history.log`.
- Component logs: `/var/log/factorlab/<component>/<service>.jsonl`, read via the
  `factorlab-logs` skill ([log-access.md](../docs/operations/log-access.md)). Rotation: hourly
  check, daily or 256 MB, 30 days; Docker json-file 20 MB x 5; journald 1 GB / 30 days.
- Collector: `systemctl status factorlab-docker-images.timer`; tunnel:
  `systemctl is-active factorlab-cloudflared`; outside check:
  [edge-canary.yml](../.github/workflows/edge-canary.yml) (daily).

## Tests

[tests/deploy/](../tests/deploy/) (deployer against a fake Docker, bundles, log reader, image
pruning), plus [tests/test_deploy_host.py](../tests/test_deploy_host.py) (prepare-host and
logrotate in step with the manifests). CI also runs shellcheck and actionlint.

```bash
uv run pytest tests/deploy tests/test_deploy_host.py
```

## Release and rollback

Owner-authorized ingestion tests before merging use the separate branch workflow in
[testing/README.md](testing/README.md) and [testing/ingestion_test.py](testing/ingestion_test.py).
It replaces only ingestion services, saves the preceding host model and running image
IDs, and verifies unchanged API, ClickHouse and secret-agent identities. Its root user
override bridges the currently installed agent's root-only secret files; it does not
complete the per-component platform rollout.

`.\deploy\release.ps1 -Component platform [-Bump ...] [-DryRun]` bumps `version:` in
[component.yaml](component.yaml), pushes `platform/vX.Y.Z`;
[component-release.yml](../.github/workflows/component-release.yml) runs the tests and
[_deploy.yml](../.github/workflows/_deploy.yml) calls `deploy-platform.sh`. The installed
deployer swaps `deploy/` in place (keeping `production.env`), seeds missing component
fragments pinned to the monolith image, runs `prepare-host.sh` and `compose config`; on
failure it restores the previous `deploy/`. There is no platform rollback workflow; fix
forward. Components follow their manifest's class: `auto`, `writer` (`data_contract`), or
`forward-only`. The legacy monolith path (`release/*` tags,
[release.yml](../.github/workflows/release.yml), `deploy-release.sh`) still runs production
until the rollout completes.

## Pitfalls

- **The hub has no in-app login.** Cloudflare Access through the outbound tunnel is its only
  authentication. Keep every published port on `127.0.0.1`, no public 80/443/8000/8080, no
  A/AAAA records, no Caddy; any unauthenticated 200 is an incident ([edge/README.md](edge/README.md)).
- The first platform release seeds the component fragments and writes `state/images.env`
  (when `/opt/factorlab/current-image` holds the monolith digest); from then on
  `deploy-release.sh` and `rollback-release.sh` refuse to run. A component deploy refuses
  until a platform release is installed. Treat that first release as the cutover.
- A platform release recreates nothing; services whose config drifted are listed in
  `releases/platform/<v>/drift` and change on their next component release or maintenance window.
- `run-political-ingest.sh` uses `production.env` and `compose.production.yml`, not the
  deployer's model and pins.
- `prepare-host.sh` removes the sshd drop-in if `sshd -t` rejects it; keep the component list
  in it and in `logrotate/factorlab` in step with `components/*` (tested).
- Edit fragments, then `render-compose`; CI fails when `compose.production.yml` is stale.
