# FactorLab production deployment

ClickHouse, the private FactorLab Data Hub/API, India and US ingestion,
political jobs, and the Cloudflare runtime-secret agent run on one VPS.
Secret values are rendered only into Docker tmpfs volumes.

## Automated production releases

For an explicitly requested production ingestion test before merging, see
[testing/README.md](testing/README.md). This separate branch workflow keeps `main`
intact and records the preceding image pins and host configuration. Normal releases
continue through the helper below.

Production releases are initiated only from a clean, synchronized `main`:

```powershell
.\deploy\release.ps1
```

The helper fetches `origin/main`, refuses dirty, non-`main`, unpushed, behind,
or diverged states, and pushes an immutable
`release/<UTC timestamp>-<short SHA>` tag. The tag starts
`.github/workflows/release.yml`, which runs the Python and frontend test suites,
builds one Linux/AMD64 image, publishes it privately to GHCR, and deploys the
exact image digest. Do not move or reuse release tags.

The ClickHouse v2 migration runner is intentionally outside this workflow.
The production application cutover completed on September 24 for the September
25 collection day; its validation record is in
[`clickhouse-v2-data-completion.md`](../docs/operations/clickhouse-v2-data-completion.md).
The first v2 release required paused legacy writers and cron, checked the new
API before starting universe, India, and US writers, then installed political
cron. After v2 activation, failures stop affected writers for a v2 fix-forward
release. Legacy collection must not be restored.

### One-time GitHub and VPS setup

In the GitHub repository, allow Actions to read and write packages. Add these
Actions secrets:

- `VPS_HOST`: production hostname or IP address.
- `VPS_USER`: the dedicated deployment account.
- `VPS_SSH_PRIVATE_KEY`: its private deployment key.
- `VPS_SSH_HOST_KEY`: the complete, pinned `known_hosts` line for `VPS_HOST`.

Verify the server's SSH fingerprint through the VPS console or another trusted
channel before storing the host-key line. The workflow never disables SSH
host-key checking. Install the matching public deployment key in the account's
`authorized_keys`, and grant that account non-interactive `sudo` access for the
reviewed deployment command.

Private GHCR pulls need a separate, read-only credential on the VPS. Create a
classic GitHub PAT with only `read:packages`, then authenticate the root Docker
client once (the token is read from stdin and must not be saved in shell
history):

```bash
read -rsp 'GHCR read token: ' GHCR_TOKEN; echo
printf '%s' "$GHCR_TOKEN" | sudo docker login ghcr.io -u arjundoshi221 --password-stdin
unset GHCR_TOKEN
sudo install -d -m 0755 /opt/factorlab/releases
sudo bash /opt/factorlab/deploy/scripts/prepare-host.sh
```

Keep the GHCR package private, retain release-tagged images for rollback, and
delete only untagged local layers when disk cleanup is needed. The workflow
publishes with its short-lived `GITHUB_TOKEN`; the VPS credential cannot publish.

### Inspect a release

The Actions job summary records the tag, commit, new digest, previous image,
verification result, and rollback result. On the VPS:

```bash
sudo cat /opt/factorlab/current-release /opt/factorlab/current-image
sudo cat /opt/factorlab/releases/RELEASE_ID/release.env
cd /opt/factorlab/deploy
sudo docker compose --env-file production.env -f compose.production.yml ps -a
sudo docker compose --env-file production.env -f compose.production.yml logs --tail 100 api ingest-india ingest-us
```

The deployer serializes releases with `flock`, validates both staged and
installed Compose configurations, starts the secret agent first, and verifies
the agent, all service image pins, API health, v2 ClickHouse readiness, and
hub reads. Before first v2 writer activation, a failed release restores the
preceding bundle and image pins. After activation, inspect fresh v2 writes and
raw-to-curated lineage with the runbook checks and fix forward on failure.

### Manual rollback before v2 activation

The production v2 activation marker is set, so the rollback script now refuses
to run. Repair production with a v2 fix-forward release.

Before activation, use the release ID being undone. The saved record contains
the exact bundle and per-service images that were active before it:

```bash
sudo bash /opt/factorlab/deploy/scripts/rollback-release.sh 20260920T120000Z-0123456789ab
```

The rollback command takes the same release lock, removes services introduced
by that release, restores the prior bundle, recreates only the prior FactorLab
services, and verifies both API endpoints. It does not recreate ClickHouse,
Uptime Kuma, Portainer, or persistent volumes.

## Per-component releases

Each component (`components/<name>/component.yaml`), the platform
(`deploy/component.yaml`) and each Cloudflare Worker ships on its own `<name>/vX.Y.Z`
tag, from a clean `main` that matches `origin/main` and has a green `ci.yml`:

```powershell
.\deploy\release.ps1 -Component api -Bump minor -DryRun   # show the version and changelog
.\deploy\release.ps1 -Component api -Bump minor           # commit, tag api/v0.2.0, push
```

`tools/release.py prepare` refuses a version that is already tagged. It also refuses
a release when nothing the unit ships has changed since its last tag. The unit's
closure is its directory, the libraries and providers it depends on, and its locked
third-party packages (`uv run python tools/affected.py closure <name>`). It then
writes the version and prepends a `CHANGELOG.md` section. The tag starts
`.github/workflows/component-release.yml`, which runs these jobs in order:

1. It checks that the tag is on `main` at the unit's declared version.
2. It runs the tests.
3. It builds, publishes, attests and verifies the image. An existing version tag
   is never overwritten.
4. It runs the host deployer below.

To re-run a tag whose transfer failed, dispatch the workflow manually; this reuses
the published image. `component-rollback.yml` (manual) returns a component to its
previous release or to any version the host has recorded. Worker tags are validated
and bundled; the deploy stays manual (`npx wrangler deploy` with the local
`wrangler.toml`).

The host side is
`deploy/host/factorlab_deploy.py` (stdlib Python, run as root) behind two stable
sudoers entry points:

```text
deploy ALL=(root) NOPASSWD: /opt/factorlab/deploy/scripts/deploy-component.sh, \
                            /opt/factorlab/deploy/scripts/deploy-platform.sh
```

```bash
sudo /opt/factorlab/deploy/scripts/deploy-component.sh api 1.2.0 ghcr.io/arjundoshi221/factorlab-api@sha256:... api.tgz
sudo /opt/factorlab/deploy/scripts/deploy-component.sh rollback api [1.1.0]
sudo /opt/factorlab/deploy/scripts/deploy-platform.sh 1.0.0 platform.tgz
sudo factorlab-compose ps          # docker compose over the live model
sudo python3 /opt/factorlab/deploy/host/factorlab_deploy.py status
```

Bundles come from `uv run python tools/components.py bundle <name|platform> <out.tgz>`.
A component deploy validates the image digest, bundle, fragment policy (loopback-only
ports, allowed bind mounts, a logging block) and image labels before touching
production. It then swaps that component's fragment and its pin in
`/opt/factorlab/state/images.env`, recreates only its services, and verifies them.
On failure it follows the manifest's rollback class. `auto` restores the previous
release. `writer` does the same if the `data_contract` is unchanged, and otherwise
stops the component and reports `fix-forward-required`. `forward-only` reports the
failure and changes nothing else. A platform release installs `deploy/`, keeps
`production.env`, runs `prepare-host.sh`, and recreates nothing. Services whose
running configuration differs from the model are listed in
`releases/platform/<v>/drift`, for the next component release or a maintenance
window. Once `state/images.env` exists, `deploy-release.sh` and
`rollback-release.sh` refuse to run.

```text
/opt/factorlab/components/<name>/      the live fragment and component.json
/opt/factorlab/state/images.env        FACTORLAB_<NAME>_IMAGE digest pins (deployer-owned)
/opt/factorlab/releases/<name>/<v>/    bundle, previous pin, result; history.log per component
```

## Server layout

```text
/opt/factorlab/deploy                  compose and non-secret configuration
/var/lib/factorlab/clickhouse          curated ClickHouse data (75 GB root disk)
/mnt/factorlab-data/clickhouse-raw     raw HTTP archive (50 GB added disk)
/etc/factorlab/identity                Cloudflare Access service-token files
/etc/factorlab/us-universe.yaml        non-secret US collection universe
/var/lib/factorlab/docker-images        host-generated image inventory snapshot
```

## Docker image inventory

`prepare-host.sh` installs and starts a root-owned systemd timer that runs once
per minute. Its service reads local Docker image and container metadata and
atomically replaces `/var/lib/factorlab/docker-images/snapshot.json`. The API
mounts that directory read-only at `/run/docker-images`; it has no Docker socket
mount. The snapshot contains IDs, tags, digests, sizes, platforms, creation times,
container names/status/start times, each container's Compose service and image
reference, and the `org.opencontainers.image.*` revision, version, source,
created, and title labels. It also carries the current release record and the
latest 12 release records (ID, commit, image, previous release and image, and
activation time), read only from whitelisted, validated `release.env` keys. It
excludes container environment variables, mounts, and all other labels. The
inventory covers images stored on this VPS, including unused and untagged images,
but not unpulled registry images. `deploy-release.sh` reinstalls the collector
through `prepare-host.sh` on every release.

Release images bake `FACTORLAB_RELEASE_ID` and `FACTORLAB_COMMIT` into their
environment and OCI labels. The overview footer and the Docker images page show
the API's own build, and the page flags a build that differs from the host's
current release record.

The release deployer writes `releases/RELEASE_ID/activated-at` after successful
verification; rollback updates the restored release's activation time. Older
releases without this record display a blank activation time. The private
`/docker-images` page and `GET /hub/api/v1/docker-images` expose the snapshot.
The API flags snapshots older than three minutes as stale; missing or malformed
snapshots return 503. Check the collector with
`systemctl status factorlab-docker-images.timer` and
`journalctl -u factorlab-docker-images.service`.

## Data catalog

The hub's Data section (`/data`, `/data/tables/<db>.<table>`, `/data/pipelines`)
serves `GET /hub/api/v1/catalog/*`. Table descriptions ship in the image as
`src/factorlab/api/catalog_descriptions.json`, generated from the schema design
doc and DDL comments with `python scripts/generate_catalog_descriptions.py`,
plus hand-written `catalog_curated.json`. A test fails when either falls behind
the v2 schema.

Row previews, CSV downloads (at most 10,000 rows), column profiles, and activity
charts run against the application ClickHouse credential. They are gated only by
Cloudflare Access at the edge (no per-user authorization in the API), so every
query is bounded in the API:
- table and column names must exist in the live catalog;
- filter values are bound parameters;
- large tables are read one time window at a time;
- each query carries `readonly=2`, `max_execution_time`, `max_rows_to_read`,
  result-size, and memory limits, tagged `log_comment='hub-catalog:<kind>'`;
- at most three preview queries run at once.

`raw.archive` previews select only metadata columns; response bodies, headers,
and metadata JSON never reach SQL, and URLs omit query strings. String cells are
scrubbed of token-like values. Inspect catalog query cost on the VPS with:

```sql
SELECT log_comment, count(), max(query_duration_ms), max(read_rows)
FROM system.query_log
WHERE log_comment LIKE 'hub-catalog%' AND event_time > now() - INTERVAL 1 DAY
GROUP BY log_comment;
```

Switches in `production.env` (applied on the next `api` recreate):
`FACTORLAB_CATALOG_PREVIEW=off` disables rows, CSV, and profiles;
`FACTORLAB_CATALOG_CSV=off` disables CSV only; `FACTORLAB_CATALOG_CSV_MAX_ROWS`
lowers the CSV cap; `FACTORLAB_CATALOG_PREVIEW_DENY` takes comma-separated
`db.table` or `db.*` patterns. Metadata stays visible when previews are off.
The catalog is not part of the release verification gate, so a catalog failure
cannot stop ingestion writers.

## Cloudflare no-domain contract

`cloudflare/upstox-auth-worker` runs on a protected `workers.dev` hostname.
Cloudflare Access authenticates the management browser and the VPS agent. The
agent reads these root-only bootstrap files:

```text
/etc/factorlab/identity/cloudflare-access-client-id
/etc/factorlab/identity/cloudflare-access-client-secret
```

They are limited to the Worker Access application. Upstox, EODHD, and
ClickHouse credentials remain in Cloudflare Secrets Store and reach containers
only through tmpfs.

## Edge access: Cloudflare Tunnel and Access

`https://arjundoshi221.com` serves the hub through an outbound-only Cloudflare
Tunnel (`factorlab-cloudflared.service`). Cloudflare Access is the only login:
the API still binds to `127.0.0.1:8000`, and the host opens no inbound
80/443/8000, so the origin cannot be reached around Access. The hub has no
in-app authentication; do not publish it any other way (no public reverse
proxy, no public port).

One-time setup in the Cloudflare dashboard:

1. The `arjundoshi221.com` zone uses Cloudflare nameservers.
2. Zero Trust -> Networks -> Tunnels -> create tunnel `factorlab-hub`
   (cloudflared). Copy its token.
3. Public hostnames `arjundoshi221.com` and `www.arjundoshi221.com` ->
   `http://127.0.0.1:8000`. Delete any A/AAAA records that point at the VPS.
4. Access -> Applications -> Self-hosted `FactorLab Hub` covering both
   hostnames, with one **Allow** policy whose include rule is the Access group
   `FactorLab users` (an **Emails** list of the named users). Login method:
   One-time PIN (optionally add an identity provider). Session duration: 24h.
   Add or remove users by editing the group.
5. Rules -> Transform Rules -> response headers: set
   `Strict-Transport-Security: max-age=31536000`,
   `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and
   `Referrer-Policy: no-referrer`.

On the VPS, store the token in a root-only file and install the unit:

```bash
sudo sh -c 'umask 077; printf "TUNNEL_TOKEN=%s\n" "$(cat)" > /etc/factorlab/identity/cloudflared.env'
sudo /opt/factorlab/deploy/scripts/install-edge-tunnel.sh
```

Paste the token on stdin (Ctrl-D to finish) so it never enters shell history.
The installer also disables any previously installed Caddy proxy.

The Access application covers `/api/v1/*` too, so bearer-key clients either
keep using the SSH tunnel or add a **Service Auth** policy with a dedicated
service token and send `CF-Access-Client-Id` / `CF-Access-Client-Secret`
alongside `Authorization: Bearer`.

Verify:

```bash
systemctl is-active factorlab-cloudflared
sudo ss -tlnp | grep -E ':(80|443|8000)\b'   # only 127.0.0.1:8000 expected
curl -sI https://arjundoshi221.com/hub/api/v1/catalog   # 302 to *.cloudflareaccess.com
```

## Legacy manual build and upload

The production image uses a Node build stage for the React hub and a Python
runtime stage for FastAPI and ingestion. Tag releases immutably and set
`FACTORLAB_IMAGE` in `production.env` to that tag; keep the previous image for
rollback.

```bash
docker build --platform linux/amd64 -t factorlab:latest .
docker save factorlab:latest | gzip > factorlab-image.tar.gz
scp factorlab-image.tar.gz ubuntu@SERVER_IP:/opt/factorlab/
scp -r deploy ubuntu@SERVER_IP:/opt/factorlab/
```

On VPS:

```bash
cd /opt/factorlab
gunzip -c factorlab-image.tar.gz | docker load
bash deploy/scripts/prepare-host.sh
cp deploy/production.env.example deploy/production.env
```

Set `CLOUDFLARE_SECRETS_URL` to the final protected `workers.dev` URL in
`production.env`. Install the two Cloudflare Access identity files before start.

## Start

```bash
cd /opt/factorlab/deploy
docker compose --env-file production.env -f compose.production.yml config
docker compose --env-file production.env -f compose.production.yml up -d
docker compose --env-file production.env -f compose.production.yml logs cloudflare-secrets-agent clickhouse bootstrap ingest-india
```

The agent polls Cloudflare every 60 seconds. Once a new daily Upstox token is
created in the management UI, it reaches the India container via tmpfs. The
client adopts it before its next safe request.

## Political ingestion schedule

Political ingestion is a one-shot Compose job. Install its host cron after the
deployment files are present in `/opt/factorlab`:

```bash
sudo sh /opt/factorlab/deploy/scripts/install-political-cron.sh
```

The installer requires the VPS timezone to be UTC. It schedules the job daily
at 02:15 UTC, prevents overlapping runs with `flock`, and appends output to
`/var/log/factorlab/ingest-political/cron.log` (rotated with the other component logs;
`prepare-host.sh` migrated the old `/var/lib/factorlab/logs/political-cron.log`). It also
normalizes the dedicated
lock file to root ownership, which safely replaces stale locks created by older
user-crontab deployments.

Run and inspect it without waiting for cron:

```bash
sudo sh /opt/factorlab/deploy/scripts/run-political-ingest.sh
sudo tail -n 100 /var/log/factorlab/ingest-political/cron.log
```

## IBKR broker mirror (optional)

`ibkr-snapshot` (the `ibkr` Compose profile) writes `broker.*` from a read-only
IB Gateway. By default that Gateway runs on the operator's machine and is
reached over Tailscale (`IBKR_HOST_*`, `IBKR_PORT_*` in `production.env`). No
IBKR credentials are stored on the VPS. The profile is off until
`FACTORLAB_IBKR_ENABLED=true`. When it's on, `deploy-release.sh` and
`rollback-release.sh` add `--profile ibkr` and verify that `ibkr-snapshot` runs
on the release image.

Optional VPS-hosted Gateways (`ibkr-gateway` profile,
`FACTORLAB_IBKR_VPS_GATEWAYS=true`) are treated like ClickHouse. They are
started with `up -d` and never force-recreated, and pinned separately with
`IBKR_GATEWAY_IMAGE`, so their logged-in sessions survive releases.

Setup, network rules and troubleshooting:
[`docs/operations/ibkr-gateway-setup.md`](../docs/operations/ibkr-gateway-setup.md).

## DBeaver

```bash
ssh -L 8123:127.0.0.1:8123 ubuntu@SERVER_IP
```

Connect DBeaver to `localhost:8123`, database `factorlab`, using a dedicated
read-only ClickHouse account. Do not expose ClickHouse publicly.

The hub is published only through Cloudflare Access (see Edge access). The
API still binds to loopback; for direct access without Access, open:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@SERVER_IP
```

Then browse to `http://127.0.0.1:8000/`.

## US universe configuration

`universe-us` resolves the provider-neutral version-2 configuration at
`/etc/factorlab/us-universe.yaml`. GitHub CSV supplies current membership by
default, Schwab validates every equity, and `ingest-us` uses Schwab for both
daily and minute prices. Change only `provider` to select another configured
adapter; there is no automatic fallback. After editing the configuration,
restart the resolver:

```bash
docker compose --env-file production.env -f compose.production.yml restart universe-us
```

The collector detects the new membership without a restart.
