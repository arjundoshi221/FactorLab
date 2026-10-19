# FactorLab VPS

> Status: `[alpha]`  
> Last verified: 2026-08-20

Canonical map of what runs on the FactorLab VPS, where it lives, how secrets
move, and current deployment state. Update this file after every production
deployment or topology change.

**Maintenance rule:** every VPS change must update this document in the same
change set. Record deployed image, Compose/service changes, secret bindings,
ports, storage, health state, and remaining blockers. Never record secret
values.

## Server

Market Data rollouts, 2026-10-19: UI trial `ui-trial/208af21cd7f3` (Market Data page
redesign, [workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/39277105860)),
then `ui-trial/29bda76225d5` (unresolved political trades link by filing ticker; committees
link through trading legislators,
[CI](https://github.com/arjundoshi221/FactorLab/actions/runs/39278250560),
[workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/39282612960)). Both
workflows succeeded; only api-candidate and web changed.

Political identity repair, 2026-10-19/20 (`docs/operations/identity-repair.md`): the
`ingest-political` image the daily cron runs is now the branch-trial image from commit
`86d9ce08902f` (`political-trial/86d9ce08902f`,
[workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/39305653277), success).
The first trial (`political-trial/342ab392b39c`,
[workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/39290010286)) retired
150 duplicate US listings and relinked trades but left 433 superseded trade rows and failed
verification; the second deleted them. Checked afterwards: 641 trade rows for 641 trades,
563 linked (was 472), 0 on retired listings, 0 US symbols with two active listings; 63
stock trades remain unresolved because their tickers have no FactorLab US listing.

Merged-branch rollout, 2026-10-19: API candidate and web run commit
`314faa41b7ac` (merge of all local work with both deployment branches), version
`0.0.0-trial.314faa41b7ac`. [CI](https://github.com/arjundoshi221/FactorLab/actions/runs/39266181438);
[trial workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/39270938896).
API image: `sha256:6a9432d84ff76a0702d7d94e8610f7ce5910e603db8002fb6c3af6ee4c960b10`;
web image: `sha256:ad1caaad833032cc4828d21d2a990453731f2235a41518c3a5464826bc1e75f7`.
Verification passed; rollback not needed. Only api-candidate and web changed;
ingestion services keep their existing images (per-unit cutovers remain separate).
`IBKR_SNAPSHOT_TIMES` is still unset on api-candidate (default slots apply).

Status/matrix/ledger rollout, 2026-10-19: API candidate and web run commit
`8bea6df4a265ca2b504d29d561143bbcdd3807f5`, version `0.0.0-trial.8bea6df4a265`
(F-038 status verdict, F-039 ticker data matrix, F-040 trade ledger, plus the
F-036 portfolio UI cleanup). [CI](https://github.com/arjundoshi221/FactorLab/actions/runs/39263043641);
[trial workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/39263389282).
API image: `sha256:037a9308448a3fc33c616b2e2301cbed0e0ba557fdbe35cdd8a1a4296ba2733e`;
web image: `sha256:6a4f4146a740967e43fef983057f531024878ba32a2b6a178d3b90c5c49bcb2a`.
Verification passed; rollback not needed. Only api-candidate and web changed. The
candidate keeps its existing environment, so `IBKR_SNAPSHOT_TIMES` is not set there
yet and the API judges IBKR freshness against the default 06:00/16:30 slots; set it to
the hourly production value on the candidate to match `ibkr-snapshot`. No migrations,
ports, secrets or routing changed; authenticated browser testing was not available.

Monitoring rollout, 2026-10-16: API candidate and web run commit
`cb3525b8c7ded794d2cf545600fc524d818ba9bd`, version
`0.0.0-trial.cb3525b8c7de`. [Verified UI workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/38855967183).
API image: `sha256:41b9e507a1005f555319a1a26ba42c5c9fdb27c811545a1518e7238cbe65f9fd`;
web image: `sha256:becf6961078c8759e842165393026d3b25b2e73019c0e0c545d0e08eec7dea8d`.
The served bundle contains the service grid and error inbox; services, daily history,
coverage and error APIs passed live checks. The universe-filtered inbox returned
seven error groups with no unavailable sections. Rollback record:
`/opt/factorlab/deployment-tests/ui-cb3525b8c7de/`.

US collector and universe services run commit
`1fe998a3c9af64a4ac33b7eb0d63686dacbc426e`, version
`0.0.0-test.20261016-1fe998a`, image
`sha256:cd9879a15abf297099d76273ecc63ced2f288330eeded6cddfe22422974b89b5`.
[Full CI passed](https://github.com/arjundoshi221/FactorLab/actions/runs/38856343053).
The component-scoped branch runner passed plan, activation and verification; both
services had healthy monitor heartbeats and zero restarts after stabilization.
Private rollback model and protected-container records:
`/opt/factorlab/deployment-tests/20261016-1fe998a/`.

The first UI attempt (`54447e44c369`) failed the new live coverage check and
restored the preceding images automatically. Qualified ClickHouse aggregate inputs
fixed the query before the verified replacement. The US update preserved its
previously deployed engine baseline and changed only monitoring behavior; CI
preparation also normalized generated path examples and repository test imports.
Protected primary API, ClickHouse, India, broker, monitor and secret-agent container
identities remained unchanged; the ingestion step also protected the new API/web.
No migrations, data deletion, ports, secret bindings or public routing changes.
Origin APIs and served assets were verified; authenticated browser testing was not
available. This remains the supported branch deployment path before component pins.

Portfolio deployment, 2026-10-15: the isolated deployment branch released API and
web commit `743777d49c1b14eb4fcfafa31ae1b79cf3a010fd`, version
`0.0.0-trial.743777d49c1b`, via
[the successful UI trial workflow](https://github.com/arjundoshi221/FactorLab/actions/runs/38764040856).
Applied schema Waves 12/13 successfully; no unrelated waves were applied. API
digest `sha256:a62bd2114b66e151fb742456bca7241809e5800cb53192135b6237747e9bdf56`;
web digest `sha256:9baa294b4c2bbdeb50b0925d1726119450447dd76a4653fa56848be7cb00fe2b`.
Only candidate API and web containers changed. Protected container identities,
health, version, existing Market Data reads, all four portfolio routes, and live
synthetic catalog/assignment/PnL/history/clear checks passed. Rollback records:
`/opt/factorlab/deployment-tests/ui-743777d49c1b/`. Public Access sign-in redirects
remain intact; authenticated browser testing was unavailable. No ports, secret
bindings or routing changed; normal component-pin rollout remains pending.

The `IBKR stream tester` profile/account `SIM_IBKR_STREAM` has events 1-3 seeded:
AAPL order `1800219929310147`, 4 of 10 shares filled, unassigned for manual testing.
Demo theme/strategy/trade-name catalogs remain available. The workstation `demo`
checkpoint must be preserved for resuming this scenario.

Preparation found the root disk full again from a 43.61 GiB ClickHouse Docker
console log. Archived and verified it before truncation; private compressed
archive and SHA-256 metadata are under
`/mnt/factorlab-data/maintenance/portfolio-20261015/`. No database data or rollback
images were deleted. Reopened ClickHouse's failed file logger with its supported
SIGHUP signal, stopping the repeated file-access stack traces without a restart.
The container identity/start time/restart count stayed unchanged. Root free space
was 36 GiB after release verification. This legacy ClickHouse container still
needs Docker console rotation at its next planned recreation.

Disk-pressure repair, 2026-10-14 19:05 UTC (October 15 IST): owner-authorized
cleanup reclaimed about 33 GiB on the root disk. Root use fell from 94% (4.4 GiB
free) to 49% (38 GiB free). The separate raw/archive disk remained 26% used with
35 GiB free. Removed unused Docker build cache older than 24 hours and 20 obsolete
local monolith images through the tested image-retention script. Active and
stopped-container images, the political cron image, and images referenced by
saved branch deployment/rollback records were preserved. No market, broker,
political or raw data was deleted.

The daily image-prune script now includes `factorlab` and `factorlab-test-*`
repositories and protects image IDs/tags in private branch JSON records. Seven
focused retention tests passed. Cleared only the accumulated
`system.processors_profile_log` diagnostics (approximately 7.8 GiB) and changed
its live and persisted TTL from 30 days to one day; other diagnostic TTLs stay
seven days. ClickHouse configuration was reloaded without recreating the database.
Configuration/script backups are private under
`/opt/factorlab/maintenance/20261015-space/`.

Restarted only the failed US price collector at 19:05:26 UTC on its existing
`035dd0f` image after its disk-full restart failure. API health remained OK;
the universe service remained ready with 503 resolved stocks. Existing data
coverage and provider errors are separate from disk-pressure recovery.
Final verification: root usage 50% with 37 GiB free, collector/API/universe/monitor
heartbeats up, and fresh daily market-bar writes after 19:06 UTC. Daily recovery
still reports pre-existing missing/invalid candle coverage; disk cleanup does not
claim complete historical coverage. The updated image-prune dry run finds no
additional eligible images.

US universe fix, 2026-10-14 18:14 UTC: the owner requested deployment of the
validated-alias publication fix. Replaced only `ingest-us` and `universe-us`
through the scoped branch runner, reusing the prebuilt image from tracked commit
`035dd0f64b26557d42960929a4ccf2264d322c4c`, version
`0.0.0-test.20261001-035dd0f`, image
`sha256:a8cd8f5d052a66087e6530d2cf37402ef1900adf1f1d05e5c762db128559108c`.
The preceding US image was `45efcac8855d`. Private backups, immutable image pins,
scope and verification records are under
`/opt/factorlab/deployment-tests/20261014-035dd0f/`.

Verification: image command imports and non-root engine validation passed;
both US containers passed startup stability checks with zero restarts.
API, web, monitor, ClickHouse, secrets agent, India and IBKR container identities
were preserved. API health and overview reads passed. Universe run
`4501e751-bfbd-4b52-8647-bd76e76bf5f3` completed successfully at
18:15:38 UTC with zero failed series; source status became `ready`, reporting
503 configured US stocks resolved. Previously, 149 bare constituent tickers were
unresolved despite having Schwab aliases. The deployed fix publishes using those
validated aliases. This is a branch-image activation, not a normal tagged release.
No secret bindings, ports, public routing, schema or data deletion changed.
Normal release bootstrap, the existing origin bypass and unrelated collector
errors remain separate work.

IBKR schedule maintenance, 2026-09-30 13:42 UTC: changed only
`IBKR_SNAPSHOT_TIMES` in `/opt/factorlab/deploy/production.env` to all 24 hourly
slots (`00:00` through `23:00`, America/New_York, XNYS trading days).
Recreated only `ibkr-snapshot` with its existing image
`sha256:0b916b860d54e290def60c0f198c74cc786b66a545e55079cca612485eb72ee6`;
`--run-on-start` triggered an immediate retry. No secret bindings, ports or
storage paths changed. The preceding settings are backed up on the host in
`production.env.before-ibkr-hourly-20260930` (mode `0600`).
Verification: container healthy; the retry connected to the live Gateway and
wrote 8 positions and 144 account-state rows at approximately 13:43 UTC.
The run was still `running` at 13:44:55 UTC; initial positions, account-update
and execution requests logged timeouts, and completion of the execution stage
remained unconfirmed. Hourly scheduling takes effect after the current run exits.
Follow-up retry at approximately 14:10 UTC: a plain restart retained the
temporary lock left by the blocked prior run. Recreated only `ibkr-snapshot`
again on the same image to clear its container-local `/tmp` lock and trigger
a fresh snapshot. The prior run logged Gateway/server connectivity error 1100;
this is distinct from a pacing violation. Hourly configuration remains in place.
Fresh retry `c61dfbe9-625d-4ca0-8f9c-83daf33706ae` started at 14:10:48 UTC
and wrote 8 positions and 144 account-state rows at 14:11:19 UTC. Initial
subscription/execution requests again timed out; the full run remained pending
at 14:12 UTC. Partial writes do not establish a complete, fresh account response.

```text
Host:       145.239.75.163
SSH user:   ubuntu
Hostname:   vps-983d702a
App root:   /opt/factorlab
Compose:    /opt/factorlab/deploy/compose.production.yml
Settings:   /opt/factorlab/deploy/production.env
```

Do not record passwords, private keys, API keys, Cloudflare service-token
values, or secret hashes here.

## Deployment map

```text
Cloudflare Secrets Store
        |
        | protected Worker + Access service token
        v
cloudflare-secrets-agent
        |
        | secret files in Docker tmpfs volumes
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
   ClickHouse          Hub + API           ingestion jobs
   :8123/:9000         :8000               India / US / political
        |
        v
curated + raw FactorLab tables
```

The hub, API, ClickHouse, and administration services bind to `127.0.0.1`.
The hub/API is published only through an outbound Cloudflare Tunnel
(`factorlab-cloudflared.service`) behind Cloudflare Access at
`https://arjundoshi221.com`; everything else requires an SSH tunnel. No inbound
80/443/8000 is needed and none should be opened. ClickHouse must never be
exposed publicly.

## Compose services

| Service | Lifetime | Purpose | Current state |
|---|---|---|---|
| `cloudflare-secrets-agent` | Long-running | Fetch secrets and render tmpfs files | Healthy |
| `clickhouse` | Long-running | Primary FactorLab database | Healthy |
| `bootstrap` | One-shot | Apply ClickHouse DDL and storage policy | Completed successfully |
| `api` | Long-running | React Data Hub and FastAPI read service on loopback port `8000` | Running |
| `ingest-india` | Long-running | Upstox intraday polling | Running; authenticated |
| `ingest-us` | Long-running | Schwab US minute/daily collection and recovery | Running; Schwab authenticated, EODHD master blocked by invalid credential |
| `ingest-political` | One-shot, `jobs` profile | Political reference and House trade ingestion | Daily cron installed; manual production run verified; 290 trades stored |
| `ibkr-snapshot` | Long-running, `ibkr` profile | Read-only IBKR mirror into `broker.*` at 06:00/16:30 New York; reads the operator's IB Gateway over Tailscale | Not enabled |
| `ibkr-gateway-paper` / `ibkr-gateway-live` | Optional, `ibkr-gateway` profile | VPS-hosted IB Gateways (IBC), alternative to the operator's Gateway; VNC `127.0.0.1:5900`/`5901` | Not used |
| `uptime-kuma` | Optional, `ops` profile | Service monitoring | Not started |
| `portainer` | Optional, `admin` profile | Docker administration | Not started |

`alt-political` is a ClickHouse domain/table prefix, not a standalone permanent
container. Political ingestion runs as `ingest-political` using the configured
immutable FactorLab image, then exits. API reads `alt_political_trades` from
ClickHouse.

The service also serves the private Data Hub, authenticated Indian and US market
data, India and US collection observability, and political collection observability.

## Storage

```text
/var/lib/factorlab/clickhouse          curated ClickHouse data
/mnt/factorlab-data/clickhouse-raw     raw HTTP archive
/var/lib/factorlab/app-data            application caches and downloaded data
/var/log/factorlab/<component>        component logs (rotated; docs/operations/log-access.md)
/etc/factorlab/identity                Cloudflare Access bootstrap identity
```

Runtime secret volumes:

```text
factorlab_clickhouse_runtime
factorlab_india_runtime
factorlab_us_runtime
factorlab_political_runtime
factorlab_ibkr_paper_runtime     paper Gateway password (Gateway-only mount)
factorlab_ibkr_live_runtime      live Gateway password (Gateway-only mount)
factorlab_ibkr_client_runtime    ibkr-snapshot ClickHouse password
```

These volumes use tmpfs. Values disappear after host reboot and are repopulated
by `cloudflare-secrets-agent`.

## Secrets flow

Cloudflare Secrets Store currently owns:

```text
UPSTOX_API_KEY
UPSTOX_API_SECRET
TOKEN_ENCRYPTION_KEY
CLICKHOUSE_PASSWORD
CLICKHOUSE_PASSWORD_SHA256
EODHD_API_KEY
FACTORLAB_API_KEY
IBKR_PAPER_PASSWORD      optional; only for VPS-hosted Gateways
IBKR_LIVE_PASSWORD       optional; only for VPS-hosted Gateways
IBKR_VNC_PASSWORD        optional; only for VPS-hosted Gateways
```

The default IBKR setup needs none of these: `ibkr-snapshot` reads the
operator's own Gateway over Tailscale. Setup:
[`docs/operations/ibkr-gateway-setup.md`](operations/ibkr-gateway-setup.md).

VPS permanently stores only Cloudflare Access service-token identity:

```text
/etc/factorlab/identity/cloudflare-access-client-id
/etc/factorlab/identity/cloudflare-access-client-secret
```

Both files must be owned by `root`, mode `0600`. Service token must be allowed
by the Access application protecting
`factorlab-upstox-auth.kairo-jai.workers.dev`.

## API

```text
Hub:     GET /
Roadmap: GET /roadmap
Hub data: GET /hub/api/v1/overview
Schema explorer: GET /schema and GET /hub/api/v1/schema-map
Health:  GET /health
Trades:  GET /api/v1/political/trades
India:   GET /api/v1/india/candles/1min
India observability:     GET /api/v1/india/dashboard
US explorer:             GET /us
US dashboard:            GET /api/v1/us/dashboard
US instruments:          GET /api/v1/us/instruments
US candles:              GET /api/v1/us/candles/1min and /api/v1/us/candles/daily
Political observability: GET /api/v1/political/dashboard
Docs:    GET /docs
API auth: Authorization: Bearer <FACTORLAB_API_KEY>
Bind:    127.0.0.1:8000
```

Trade filters: `ticker`, `bioguide_id`, `chamber`, `date_from`, `date_to`,
`cursor`, and `limit`.

India candle filters: `symbol`, `time_from`, `time_to`, `source`, `cursor`,
and `limit` (maximum 1,000). Results are newest first and include OHLCV, open
interest, instrument/contract IDs, source, and ingestion timestamps.

Open an SSH tunnel from the workstation:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@145.239.75.163
```

Then browse to `http://127.0.0.1:8000/`, open the India explorer at
`http://127.0.0.1:8000/india`, or call the API on the same local base URL. The
browser never receives the API bearer secret.

## ClickHouse workstation access

ClickHouse binds to `127.0.0.1` on the VPS and must not be exposed publicly.
Python code opens the SSH tunnel automatically when `CLICKHOUSE_SSH_HOST` is
set in `.env`; `ClickHouseStorage.from_environment()` starts a forwarder on a
random local port, points the client at it, and tears the tunnel down on
`close()`.

Required `.env` keys for tunneled access from a workstation:

```text
CLICKHOUSE_SSH_HOST=145.239.75.163
CLICKHOUSE_SSH_USER=ubuntu
CLICKHOUSE_SSH_PASSWORD=<ubuntu-login-password>
CLICKHOUSE_HOST=127.0.0.1               # remote bind seen from the VPS
CLICKHOUSE_PORT=8123
CLICKHOUSE_USERNAME=<user>
CLICKHOUSE_PASSWORD=<from Cloudflare Secrets Store>
CLICKHOUSE_DATABASE=<database>
```

`CLICKHOUSE_SSH_KEY_PATH` is also honored if a key file is preferred, but
password authentication is the workstation default. If both keys are set,
`CLICKHOUSE_SSH_KEY_PATH` wins.

### Read-only query command

From the repository root, after a user requests a live data check:

```powershell
python scripts/read_clickhouse.py --sql "SELECT 1 AS value"
python scripts/read_clickhouse.py --sql-file path/to/query.sql
```

The reader loads `CLICKHOUSE_*` values from the environment or the repository
`.env` (environment wins). It requires the configured SSH host/user and
ClickHouse username/password/database; it has no development credential
fallback. The remote ClickHouse host must be loopback. The SSH client uses a
public key and strict host-key checking. Set `CLICKHOUSE_SSH_KEY_PATH` in
`.env` to select a private key, or configure your default SSH key/agent. Add
the VPS host key to your local `known_hosts` after verifying its fingerprint
through a trusted channel. Password SSH is not used by this reader, even if
`CLICKHOUSE_SSH_PASSWORD` exists in `.env`.

For a new workstation, generate a dedicated key with `ssh-keygen -t ed25519`
and have an authorized VPS administrator add its public key to the `ubuntu`
account's `authorized_keys`. Set `CLICKHOUSE_SSH_KEY_PATH` to the private key's
path if SSH will not find it by default. Connect once with `ssh` and accept
the host key only after verifying the displayed fingerprint through a trusted
channel; the reader will then use the recorded `known_hosts` entry.

Each invocation opens a local-only SSH tunnel, runs one `SELECT` or `WITH`
query with `readonly=1`, a 30-second execution limit, and server-side result
limits, then closes the tunnel. Terminal output is capped at 30 rows and
12 KB. Query errors omit credentials and server response bodies. This command
does not grant standing permission to query production data; use it only for
the user's requested read.

Container-side code (VPS-native services in the Compose stack) leaves
`CLICKHOUSE_SSH_HOST` unset and reaches ClickHouse over the private Docker
network without a tunnel. The SSH tunnel is a workstation convenience only;
do not enable it inside deployed services.

## Deploy or update

Build and upload an `amd64` image from project root:

```bash
docker build --platform linux/amd64 -t factorlab:latest .
docker save factorlab:latest | gzip > factorlab-image.tar.gz
scp factorlab-image.tar.gz ubuntu@145.239.75.163:/opt/factorlab/
scp deploy/compose.production.yml ubuntu@145.239.75.163:/opt/factorlab/deploy/
```

Load and validate on VPS:

```bash
cd /opt/factorlab
gunzip -c factorlab-image.tar.gz | sudo docker load
cd deploy
sudo docker compose --env-file production.env -f compose.production.yml config
```

Start API dependency chain:

```bash
sudo docker compose --env-file production.env -f compose.production.yml up -d api
```

Run political ingestion when API infrastructure is healthy:

```bash
sudo docker compose --env-file production.env -f compose.production.yml \
  --profile jobs run --rm ingest-political
```

Install the daily political-ingestion cron after deploying the repository's
current `deploy/` directory:

```bash
sudo sh /opt/factorlab/deploy/scripts/install-political-cron.sh
```

It runs at 02:15 UTC, uses a non-blocking `flock` lock to prevent overlaps, and
writes to `/var/log/factorlab/ingest-political/cron.log`. The installer refuses
to install on a host whose timezone is not UTC.

## Observe and troubleshoot

```bash
cd /opt/factorlab/deploy
sudo docker compose --env-file production.env -f compose.production.yml ps -a
sudo docker compose --env-file production.env -f compose.production.yml logs --tail 100 api
sudo docker compose --env-file production.env -f compose.production.yml logs --tail 100 cloudflare-secrets-agent
sudo docker inspect factorlab-clickhouse --format '{{json .State.Health}}'
```

Expected startup chain:

```text
cloudflare-secrets-agent healthy
clickhouse healthy
bootstrap exits 0
api running
```

## Current deployment state

Completed through 2026-08-12:

- `factorlab:latest` API-capable image uploaded and loaded.
- ClickHouse 25.3 image pulled.
- Updated production Compose installed.
- Protected Cloudflare Worker deployed with `FACTORLAB_API_KEY` binding.
- API bearer key created in Cloudflare Secrets Store.
- API, bootstrap, ClickHouse, and secrets-agent containers created.
- Cloudflare Access service identity installed as root-only files and authorized
  by a Service Auth policy.
- Secrets agent successfully synchronizes the runtime bundle.
- ClickHouse, bootstrap, and API started successfully.
- API health endpoint verified with HTTP 200.
- Initial political ingestion completed with 105 rows in
  `alt_political_trades`.
- India ingestion authenticated successfully and is running.
- API image `sha256:06cce275f2ce900da25f5d6a359e907a7743b3212c5b203a7212174b181f2772`
  deployed with authenticated Indian one-minute candle reads.
- Live `GET /api/v1/india/candles/1min?limit=2` returned Upstox TCS equity and
  futures candles with HTTP 200.

Completed on 2026-08-13:

- Deployed final API image
  `sha256:db56b6e5b3fb29e73aa196a6a4aa67a079f31ca0a71601d54736cf2cb4f88874`.
- Applied `003_india_observability.sql`, creating `india_expected_series` and
  shared `ingestion_runs` tables.
- Recreated API and India ingestion for the observability rollout. The API was
  subsequently recreated alone for the final historical-anomaly hotfix, so the
  continuously running India ingester was not interrupted again.
- Added and live-verified India dashboard, coverage, collection activity,
  freshness, gaps, anomalies, time-series metrics, instrument drill-down,
  ingestion-run, and source-health endpoints.
- Added and live-verified political dashboard, coverage, collection activity,
  freshness, anomalies, time-series metrics, legislator/ticker discovery and
  drill-down, ingestion-run, and source-health endpoints.
- Ran political ingestion successfully: 24/24 work units, 5,187 rows written;
  dashboard then reported 342 filings and 244 trades.
- Verified authenticated health, India dashboard/anomalies/coverage (including
  the historical trading date `2026-08-12`), political dashboard/anomalies,
  ingestion history, and source status with HTTP 200.

The political schedule is installed in `/etc/cron.d/factorlab-political`, cron
is enabled and active, and the runner was verified end to end under its
non-blocking lock. The continuously running India daemon is configured with
Docker's `unless-stopped` restart policy.

## Deployment change log

### 2026-09-10

- Deployed immutable image `factorlab:us-schwab-v1-20260909` to `api` and the
  new long-running `ingest-us` service. The secrets agent, ClickHouse, political
  job configuration, and India image were not recreated or retagged.
- Applied additive US collection tables: `us_expected_series`,
  `us_recovery_state`, `us_session_coverage`, and `us_source_status`.
- Added the `/us` private explorer and authenticated US dashboard, instruments,
  daily/minute candle, session-history, ingestion-run, and source-status APIs.
- Verified the API, US and India pages, hub overview, authenticated US reads,
  existing India candle reads, and existing political reads with HTTP 200.
- Verified the India container identity and original start timestamp were
  identical before and after deployment. It was already exited with code 1
  before this rollout and remains stopped pending separate diagnosis.
- The Schwab secret state was `reauth_required` at deployment. `ingest-us` is
  running with a 1 GB limit and waits for Cloudflare to deliver a refreshed
  token; initial data backfill and open-session verification remain pending.
- Release archive SHA-256:
  `86a485380c8df2c9c8f9db99702b2036a5936ed3e84729fc5122bcd76c7fb88c`.
  Rollback configuration is in
  `/opt/factorlab/deploy/rollback-us-20260910T150800Z`.

### 2026-09-15

- Deployed the dynamic full NSE cash-equity collector as
  `factorlab:full-nse-universe-20260915-final5`; the API remains on the verified
  `factorlab:full-nse-universe-20260911-final3` image.
- Production now derives `full_nse_eq` from the current Upstox NSE instrument
  master and refreshes it daily inside the continuously running India daemon.
  At verification it had 2,656 active/reference/collecting instruments and
  zero instruments marked not configured.
- Completed a full production sweep: 2,656 requested, 2,656 successful, zero
  failed. ClickHouse held 550,719 rows for the session across 2,652 instruments;
  four active listings returned no candle and are reported as no-data rather
  than configuration failures.
- Added batched Market Quote OHLC V3 collection, finalized-minute handling,
  batched ClickHouse writes, inactive-reference reconciliation, and invalid
  OHLC rejection. Full-universe historical backfill remains a separate job.
- Disabled ClickHouse metric and asynchronous-metric system logs and truncated
  only those system-log tables after they caused memory pressure. No FactorLab
  market or reference tables were removed.
- Confirmed `ingest-india` uses `restart: unless-stopped`; daily instrument
  refresh and minute sweeps run inside the daemon, so no India cron is needed.
  The independent political cron remains installed at 02:15 UTC.
- Archived the deployed collector at
  `/mnt/factorlab-data/deployment-archives/factorlab-full-nse-universe-20260915-final5.tar`.
  SHA-256: `2a134d2f905a7d8577b83ce954b7f461b86b6b1489f9c7d51e91636f1cd66cae`.

### 2026-09-18

- Deployed the live ClickHouse Schema Map as immutable image
  `factorlab:schema-map-v1-20260918` to the API service only. The India and US
  ingestion containers retained their existing identities and start times.
- Applied additive migration `005_hub_schema_map.sql`, creating
  `hub_schema_layouts` for the revision-checked shared canvas arrangement.
- Live verification reported 19 tables, 29 reviewed logical relationships,
  zero schema warnings, and layout revision 0. `/schema`, schema metadata,
  overview, India, US, and political Hub endpoints all returned HTTP 200.
- Archived the deployed image at
  `/mnt/factorlab-data/deployment-archives/factorlab-schema-map-v1-20260918.tar`.
  SHA-256: `2c99d4fae31ce3fb423aba8f65aae73840b282dc9a208ef6ee13ede2ad109943`.
  Rollback configuration is in
  `/opt/factorlab/deploy/rollback-schema-map-20260918T182000Z`.
- Fixed intermittent HTTP 500 responses on the US dashboard, instruments, and
  ingestion-run endpoints. The US FastAPI routes now create request-local
  ClickHouse clients instead of sharing one non-thread-safe client session
  across concurrent UI requests.
- Production runs `factorlab:hub-political-india-ui-20260918-hotfix1` for the
  API. The API container alone was recreated; `ingest-us` retained its original
  container identity and start time.
- Verified 90 concurrent requests across the three US UI endpoints with 90
  HTTP 200 responses and no HTTP 500 responses in the replacement API logs.
- Schwab authentication and automatic access-token refresh are healthy. The
  full listed-equity master remains blocked because the configured EODHD
  credential is invalid; the daemon continues retrying without restart.

### 2026-09-06

- Deployed immutable image `factorlab:hub-india-universe-20260903` after
  validating all collection views against the production ClickHouse data.
- Production counts at rollout were 2,684 reference instruments, 5 under
  active collection, 0 historical-only, and 2,679 not configured.
- The rollout exposed an expired daily Upstox token. The India ingester was
  stopped after its failed starts and must be started after Upstox login; the
  dashboard and stored-data APIs remain healthy.

### 2026-09-03

- Expanded the India Markets explorer from candle-bearing instruments to the
  complete synchronized Upstox reference universe.
- Added server-side views for active collection, historical-only instruments,
  and reference instruments that are not configured for collection.

### 2026-09-02

- Deployed immutable image `factorlab:hub-india-v1-20260827` to the API,
  secrets agent, and continuously running India ingestion service.
- Production-verified India Markets search, selected-session checks, and
  per-instrument daily history against ClickHouse; protected candle reads still
  require bearer authentication.
- Confirmed zero service restarts and zero API/India-ingestion errors after the
  rollout. The daily political cron remained active and completed successfully
  at 02:15 UTC.

### 2026-08-27

- Added the private FactorLab Data Hub with all-table inventory, data ranges,
  schedule-aware health, one-minute refresh, and the V1-V5 roadmap.
- Added the componentized India Markets explorer with unique-instrument search,
  selected-date coverage and value checks, and per-instrument exchange-session
  history that makes completely missing trading days visible.
- Deployed immutable image `factorlab:hub-v1-20260827` to the API, secrets
  agent, and India ingestion services.
- Bound the combined hub/API service to VPS loopback; SSH tunneling is required
  until a domain is connected to Cloudflare Access.
- Disabled ClickHouse sampling trace and mirrored text logs after they consumed
  approximately 55.5 GB; bounded retained system logs to seven days.
- Preserved all FactorLab tables and verified their row counts across cleanup.
- Installed and manually verified the daily political cron at 02:15 UTC,
  removed its broken legacy user-crontab entry, and hardened the installer for
  Ubuntu cron reload behavior and stale lock-file ownership.

### 2026-08-13

- Built and deployed the India and political observability API surface.
- Added expected-series and ingestion-run operational storage.
- Instrumented India and political ingestion jobs with run outcomes.
- Corrected ClickHouse 25.3 join syntax found during local container tests.
- Split India dashboard aggregates after production-scale validation exposed a
  4.5 GB memory limit, then redeployed and verified the hotfix.
- Added explicit anomaly-query column aliases after historical data exposed
  qualified ClickHouse result keys, then verified the affected historical
  dashboard and anomaly routes in production.
- Ran and verified a successful political ingestion job after deployment.

### 2026-08-12

- Verified the daily Upstox token and started `ingest-india` successfully.
- Added and deployed authenticated `GET /api/v1/india/candles/1min` reads from
  ClickHouse with symbol/time/source filters and cursor pagination.
- Recreated only the API container; ClickHouse and India ingestion remained
  running.
- Installed a dedicated local SSH public key for subsequent VPS deployments.

### 2026-08-11

- Prepared the production Compose configuration to publish the API on public
  TCP port `8000`; VPS deployment and firewall validation remain pending.

### 2026-08-04

- Verified VPS identity, filesystem, Docker state, images, volumes, and ports.
- Confirmed no pre-existing political container or political data volume.
- Uploaded and loaded API-capable `factorlab:latest` image.
- Pulled `clickhouse/clickhouse-server:25.3`.
- Installed updated production Compose with `api` service.
- Added `FACTORLAB_API_KEY` to Cloudflare Secrets Store and protected Worker.
- Deployed protected Worker version `75856592-418d-462a-8840-e47e01804311`.
- Created API, bootstrap, ClickHouse, and secrets-agent containers.
- Startup stopped safely because Cloudflare Access identity files are missing.
- Left secrets agent running unhealthy; dependent containers remain created and
  not started.

### 2026-08-05

- Installed the VPS Cloudflare Access service-token identity and added the
  matching Service Auth policy to the protected Worker application.
- Confirmed the runtime-secrets endpoint returns authenticated JSON and the
  secrets agent is healthy.
- Corrected the ClickHouse runtime users mount so the image entrypoint can
  create its default-user configuration.
- Corrected host read permissions for the mounted ClickHouse storage config.
- Recreated the empty raw archive table on the `raw_archive` storage policy.
- Completed bootstrap and started the loopback-only API.
- Verified API health with HTTP 200.
- Ran initial House political ingestion and stored 105 trade rows.

## Security

- Rotate VPS login password after any exposure.
- Prefer SSH keys; disable password login after key access works.
- Never store runtime secrets in `production.env` or Compose.
- Keep ClickHouse and the hub/API loopback-only. Publish the hub only through
  the Cloudflare Tunnel behind Access (`deploy/README.md`, "Edge access"); do
  not reopen public TCP port `8000` or run a public reverse proxy.
- Rotate Cloudflare VPS service token before expiry.

## UDP monitoring branch trial — 2026-09-30 14:40 UTC

Owner-authorized monitoring deployment extended the current VPS branch trial.
Images use tracked source `60afea8`, version `0.0.0-test.monitoring`; rollback
configuration is private under `/opt/factorlab/deployment-tests/20260930-60afea8/`.
Wave 11 is applied. Monitor, candidate API, web, secrets agent and heartbeat senders
are installed; monitoring uses backend UDP with no host port. The primary API and
ClickHouse identities were preserved. `/monitoring`, its assets and filtered
monitoring API reads passed public HTTP verification.

All six daemon/monitor heartbeats were up at rollout completion. Political awaits
its next scheduled run. Existing IBKR snapshot stalls can make that service late
or down even while the container runs. Sender configuration is persisted in
`production.env`, and monitor JSONL rotation is installed. Alerts use local JSONL.
Normal release bootstrap and Cloudflare Access remain pending. Details:
[service monitoring](operations/service-monitoring.md).

## Monitoring navigation update — 2026-09-30 17:00 UTC

Deployed only the web service from tracked commit `379c377`, version
`0.0.0-test.monitoring-nav`. Monitoring is a visible primary navigation tab.
Thirteen UI tests and the production build passed; origin health, image, version,
navigation assets and monitoring API verification passed. All other running
container identities stayed unchanged. Root-only rollback models are under
`/opt/factorlab/deployment-tests/20260930-379c377/`. Public requests now redirect
to Cloudflare Access sign-in; the authenticated public UI was not browser-verified.
The legacy bundled UI on loopback port 8000 still exists; the updated web is 8080.
