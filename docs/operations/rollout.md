# Rollout: from the monolith to per-component releases

Feature [F-007](../features/F-007-per-component-rollout.md). Every step changes production
and is run by the owner; nothing here runs automatically. Do the steps in order and meet
each exit criterion before the next. Background: [releases.md](releases.md),
[deploy/README.md](../../deploy/README.md), [deploy/edge/README.md](../../deploy/edge/README.md).

## Before anything: the edge (F-006)

On 2026-09-30 the hub hostname resolved straight to the VPS and served the hub without
Cloudflare Access. Restore the invariants in [deploy/edge/README.md](../../deploy/edge/README.md)
first: DNS only the tunnel's proxied CNAME, no public listener on 80/443, one Access
application over the whole hostname.

- [ ] `dig +short arjundoshi221.com` returns Cloudflare addresses only
- [ ] `curl -sI https://arjundoshi221.com/hub/api/v1/overview` answers 302 to `*.cloudflareaccess.com`
- [ ] on the VPS, `sudo ss -tlnp` shows no public listener except SSH

## R0 — merge the branch

- [ ] `ci-ok` is green for the head of `restructure/platform-v3` (GitHub → Actions)
- [ ] merge into `main` with a **merge commit** (not squash or rebase: `.git-blame-ignore-revs`
      and the per-commit history depend on the hashes)
- [ ] `ci-ok` green on `main`; then dispatch **Edge canary** once (scheduled workflows run
      only from the default branch) and confirm it passes

Production is unchanged: `release.yml` still builds the monolith from the new layout.

## R1 — one monolith release

Same topology, new code layout: JSON logs to stderr, secrets rendered 0444, per-service
`FACTORLAB_COMPONENT`. Outside market hours for India and US:

- [ ] `.\deploy\release.ps1` (no `-Component`); the job summary reports verification succeeded
- [ ] `docker compose ps` healthy; `factorlab-db verify-writes` passes (run by the release)
- [ ] the hub loads through Access; fresh `meta.ingestion_runs` rows appear

This also installs the new `deploy/` bundle on the host, including
`deploy/scripts/deploy-component.sh`, `deploy-platform.sh` and the host deployer.

## R2 — platform bootstrap

One-time owner steps on the VPS:

- [ ] sudoers for the deploy account (and remove any broad `bash` rule it had):
      `deploy ALL=(root) NOPASSWD: /opt/factorlab/deploy/scripts/deploy-component.sh, /opt/factorlab/deploy/scripts/deploy-platform.sh`
- [ ] GHCR read login for root still valid (`sudo docker pull` of a component image works)
- [ ] log-reader key added to `/etc/factorlab/log-reader/authorized_keys` ([log-access.md](log-access.md))

Then release the platform (0.1.0 → 1.0.0):

- [ ] `.\deploy\release.ps1 -Component platform -Bump major`
- [ ] the job reports succeeded; **no service was recreated**: `sudo cat /opt/factorlab/releases/platform/1.0.0/drift`
      lists what differs from the model (expected: nothing, or services that change on their
      next release), and container start times are unchanged
- [ ] `/opt/factorlab/state/images.env` pins every component to the monolith digest
- [ ] `uv run python tools/read_logs.py list` works from the workstation; logrotate and the
      prune timer are active (`systemctl list-timers | grep -E "logrotate|factorlab"`)
- [ ] `snapshot.v2.json` exists and the hub's Docker images page shows "Running components"

From here the legacy `deploy-release.sh` refuses to run, and the political cron uses the
deployer's pins.

## R3 — components, one at a time

Order, and what to watch:

1. **web** — set `FACTORLAB_WEB_ENABLED=true` in `/opt/factorlab/deploy/production.env`,
   then `.\deploy\release.ps1 -Component web -Bump major`. Check `curl -s 127.0.0.1:8080/healthz`
   on the VPS. Add the tunnel path rules (deploy/edge/README.md): API paths to `:8000`,
   catch-all to `:8080`. Check the hub, then run the edge canary.
2. **api** — only after web serves the UI: the component api image has no UI (`/` returns 503).
3. **secrets-agent**
4. **schema-migrator** (forward-only; it only runs bootstrap and readiness)
5. **ingest-broker** (only if the `ibkr` profile is enabled)
6. **ingest-political** (the next 02:15 UTC cron run uses the released image)
7. **ingest-us**, then **ingest-india** — writers: outside market hours for their market

For each: the job summary says succeeded; `tools/read_logs.py list` shows the version;
`errors --since 1h` is clean; writers write fresh `meta.ingestion_runs` rows.

- [ ] exercise one rollback on **api** and one on **web**
      (Actions → Component rollback → component, no version)

## R4 — retire the monolith

A normal change on `main` (then a platform release): delete `release.yml`,
`deploy-release.sh`, `rollback-release.sh`, the root `Dockerfile`, the `FACTORLAB_IMAGE`
fallbacks in the fragments, the script shims, the API's SPA serving, Caddy and
`install-web-proxy.sh`, and `deploy/compose.production.yml` rendering; archive the
`v2-cutover-activated` marker. Only `*/v*` pipelines remain.

## R5 — maintenance window

Planned restarts: ClickHouse `logger.xml` with a `/var/log/factorlab/clickhouse` mount and a
json-file cap; logging blocks for the IB Gateways. Record it in the sprint file.
