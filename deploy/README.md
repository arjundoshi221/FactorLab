# FactorLab production deployment

ClickHouse, the private FactorLab Data Hub/API, India ingestion, optional
US/political jobs, and the Cloudflare runtime-secret agent run on one VPS.
Secret values are rendered only into Docker tmpfs volumes.

## Server layout

```text
/opt/factorlab/deploy                  compose and non-secret configuration
/var/lib/factorlab/clickhouse          curated ClickHouse data (75 GB root disk)
/mnt/factorlab-data/clickhouse-raw     raw HTTP archive (50 GB added disk)
/etc/factorlab/identity                Cloudflare Access service-token files
```

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

## Build and upload

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
