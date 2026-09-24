# FactorLab production deployment

ClickHouse, the private FactorLab Data Hub/API, India and US ingestion,
political jobs, and the Cloudflare runtime-secret agent run on one VPS.
Secret values are rendered only into Docker tmpfs volumes.

## Automated production releases

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
mount. The snapshot contains IDs, tags, digests, sizes, creation times, container
names/status/start times, and the current release activation time. It excludes
container environment variables and mounts. The inventory covers images stored
on this VPS, including unused and untagged images, but not unpulled registry images.

The release deployer writes `releases/RELEASE_ID/activated-at` after successful
verification; rollback updates the restored release's activation time. Older
releases without this record display a blank activation time. The private
`/docker-images` page and `GET /hub/api/v1/docker-images` expose the snapshot.
The API flags snapshots older than three minutes as stale; missing or malformed
snapshots return 503. Check the collector with
`systemctl status factorlab-docker-images.timer` and
`journalctl -u factorlab-docker-images.service`.

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
`/var/lib/factorlab/logs/political-cron.log`. It also normalizes the dedicated
lock file to root ownership, which safely replaces stale locks created by older
user-crontab deployments.

Run and inspect it without waiting for cron:

```bash
sudo /opt/factorlab/deploy/scripts/run-political-ingest.sh
sudo tail -n 100 /var/lib/factorlab/logs/political-cron.log
```

## DBeaver

```bash
ssh -L 8123:127.0.0.1:8123 ubuntu@SERVER_IP
```

Connect DBeaver to `localhost:8123`, database `factorlab`, using a dedicated
read-only ClickHouse account. Do not expose ClickHouse publicly.

The hub and API are also loopback-only. Open them with:

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
