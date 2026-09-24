# IBKR Gateway setup

> Status: `[alpha]` — code and Compose wiring merged; not yet enabled in production.

How to run the read-only IBKR broker mirror. The `ibkr-snapshot` daemon on the
VPS reads the IB Gateway running **on the operator's own machine**, over
Tailscale, and writes `broker.*` in ClickHouse. IBKR passwords never leave that
machine. Optional VPS-hosted Gateways are covered in the
[appendix](#appendix-gateways-on-the-vps). Everything is off until
`FACTORLAB_IBKR_ENABLED=true`.

## How the data is fetched

IBKR has no REST pull for this data. **IB Gateway** is IBKR's Java app: you log
in to it, and it serves the TWS API as a plain TCP socket (port 4002 paper,
4001 live). FactorLab is a client of that socket (`ib_async`, always
`readonly=True`).

```text
Your machine                                   VPS
  IB Gateway (you log in; Read-Only API)
    ^ login + IBKR Mobile 2FA                    ibkr-snapshot
    |                                              06:00 + 16:30 New York
  IBKR servers                                     capture -> raw.archive -> normalize
                                                        |
  port 4002 / 4001  <==== Tailscale (WireGuard) ========+
                                                        v
                                               ClickHouse broker.* + meta.ingestion_runs
```

Each snapshot is one `meta.ingestion_runs` row with a unit for each mode and
dataset (`paper:positions`, `live:executions`, ...). If your machine is off or
the Gateway is logged out, those units fail, the run is recorded as `partial` or
`failed`, and you get an alert. `meta.source_status` for `ibkr` shows the latest
outcome. Executions look back 24 hours, so one missed run loses no fills.

The TWS API socket is unencrypted and unauthenticated: anything that can reach
the port can read the account. The rules below allow only the VPS, over
Tailscale's encrypted tunnel, and the Gateway's Read-Only API setting refuses
orders.

## 1. Prerequisites

- **IBKR Pro, funded.** IBKR does not offer API access on IBKR Lite, and paper
  API access also requires a funded Pro live account.
- **Market data** for `portfolio()` prices (for example the US Snapshot
  Bundle). Without it, `market_price` and `market_value` are `NULL`. For paper,
  turn on market-data sharing from the live account.
- **Tailscale** on your machine and on the VPS, in the same tailnet.

## 2. IB Gateway settings (your machine)

In **Configure → Settings → API → Settings**:

- tick **Enable ActiveX and Socket Clients** and **Read-Only API**;
- untick **Allow connections from localhost only**;
- set **Trusted IPs** to the VPS's Tailscale IP only (`tailscale ip -4` on the VPS);
- set the socket port to 4002 (paper) or 4001 (live);
- tick **Download open orders on connection** (so orders placed by other
  clients appear).

In **Configure → Settings → Lock and Exit**, set **Auto restart** so the Gateway
restarts daily without a new login. IBKR still forces one full login with 2FA
each week, after its Sunday 01:00 ET reset.

## 3. Network (your machine)

**Windows Firewall.** Allow the Gateway ports only from the VPS. Run in an
elevated PowerShell:

```powershell
New-NetFirewallRule -DisplayName "IBKR Gateway API (VPS via Tailscale)" `
  -Direction Inbound -Protocol TCP -LocalPort 4001,4002 `
  -RemoteAddress <vps-tailscale-ip> -Action Allow
```

**Tailscale ACL.** In the admin console, restrict the VPS to your machine's
Gateway ports:

```json
{ "action": "accept", "src": ["tag:factorlab-vps"], "dst": ["<your-machine>:4001,4002"] }
```

**VPNs on the same machine:**

- **Proton VPN:** use split tunneling → Exclude, and add
  `C:\Program Files\Tailscale\tailscaled.exe` and `100.64.0.0/10`. Use the
  Standard kill switch, not Advanced/permanent.
- **Cloudflare WARP:** exclude `100.64.0.0/10`, or turn WARP off.
- **OpenVPN on the VPS:** fine unless a kill switch drops non-`tun0` traffic.
  In that case, allow `tailscale0`.

**Stay reachable.** The machine must be awake and logged in to the Gateway at
the snapshot times (06:00 and 16:30 New York, converted to your time zone).
On Windows, `powercfg /change standby-timeout-ac 0` disables sleep on AC
power. To match your hours, change `IBKR_SNAPSHOT_TIMES`: the 06:00 snapshot
already captures the previous close's positions.

## 4. `production.env` (VPS)

Edit `/opt/factorlab/deploy/production.env`. The template is
`deploy/production.env.example`.

| Variable | Value |
|---|---|
| `FACTORLAB_IBKR_ENABLED` | `true`; releases then run `ibkr-snapshot` (`ibkr` profile) |
| `IBKR_HOST_PAPER` / `IBKR_HOST_LIVE` | your machine's Tailscale IP (`tailscale ip -4` on it) |
| `IBKR_PORT_PAPER` / `IBKR_PORT_LIVE` | `4002` / `4001` |
| `IBKR_SNAPSHOT_MODES` | `paper` to start; `paper,live` once live is set up |
| `IBKR_SNAPSHOT_TIMES` | `06:00,16:30` (America/New_York, XNYS sessions only) |

No IBKR secrets are needed in Cloudflare for this setup. The daemon only gets
`CLICKHOUSE_PASSWORD`.

## 5. Check reachability, then deploy

From the VPS, before releasing:

```bash
tailscale ping <your-machine>              # "via DERP" (relay) or direct: both fine
nc -zv <your-machine-tailscale-ip> 4002    # Gateway port reachable
```

Then release normally (`./deploy/release.ps1`; see `deploy/README.md`). With
`FACTORLAB_IBKR_ENABLED=true`, the release recreates `ibkr-snapshot` on the
release image and verifies it is running. The container reaches your machine
through the VPS's `tailscale0` route. It arrives from the VPS's Tailscale IP,
which is why that IP is the one to trust.

Helper for the VPS shell:

```bash
fl() { sudo docker compose --env-file /opt/factorlab/deploy/production.env \
  -f /opt/factorlab/deploy/compose.production.yml --profile ibkr "$@"; }
```

## 6. Verify

```bash
fl ps ibkr-snapshot
fl logs --tail 50 ibkr-snapshot     # per-unit rows and the run summary
```

`--run-on-start` snapshots as soon as the daemon starts. A healthy start logs
`paper:positions ... ok` etc. and `IBKR snapshot ok`. From the workstation:

```powershell
python scripts/read_clickhouse.py --sql "SELECT status, started_at, rows_written, error FROM meta.ingestion_runs FINAL WHERE source = 'ibkr' ORDER BY started_at DESC LIMIT 5"
python scripts/read_clickhouse.py --sql "SELECT account_mode, count(), max(snapshot_time) FROM broker.positions_snapshot FINAL GROUP BY account_mode"
python scripts/read_clickhouse.py --sql "SELECT request_key, length(response_body) FROM raw.archive WHERE source = 'ibkr' ORDER BY fetched_at DESC LIMIT 4"
```

To take an extra snapshot now, use `fl restart ibkr-snapshot`.

## 7. Local development

With the Gateway logged in on your machine:

```powershell
python scripts/us/ibkr/us_portfolio_ibkr_snapshot.py --once --dry-run --modes paper
```

`--dry-run` connects, captures and normalizes, then logs row counts without
touching ClickHouse.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `paper:connect ... unreachable` | Machine asleep or off, Gateway logged out, or the network path is blocked. Run `nc -zv` from the VPS. |
| `nc` fails but `tailscale ping` works | Windows Firewall rule, Gateway Trusted IPs, or "localhost only" still ticked |
| `tailscale ping` fails | VPN kill switch or WARP route clash (step 3) |
| Connection accepted then dropped immediately | VPS IP missing from Trusted IPs; the Gateway may be showing an "accept connection?" dialog |
| Gateway logs out when you use the IBKR app | IBKR allows one session per username; use a secondary username for the Gateway |
| `market_price` / `market_value` are `NULL` | No market-data subscription or paper data sharing |
| Rows have `resolution_confidence='unresolved'` | No approved `ibkr_conid` alias yet; conids are listed in `meta.unresolved_entities` |

To disable, set `FACTORLAB_IBKR_ENABLED=false`, then run `fl stop ibkr-snapshot`.
Releases do not remove services that are already running.

## Appendix: Gateways on the VPS

For unattended collection (for example paper while your machine is off), the
`ibkr-gateway` profile runs IBC-managed Gateways in containers
(`ghcr.io/gnzsnz/ib-gateway`). IBC types the password, but it cannot approve
2FA: live still needs an IBKR Mobile tap after each weekly reset. The password
then lives in Cloudflare Secrets Store and in VPS memory while the Gateway runs.

1. Create `IBKR_PAPER_PASSWORD`, `IBKR_LIVE_PASSWORD` and `IBKR_VNC_PASSWORD` in
   Cloudflare Secrets Store. Uncomment their bindings from
   `wrangler.example.toml` in `wrangler.toml`, then run `npm run deploy` in
   `cloudflare/upstox-auth-worker`. The secrets agent renders each password
   into that Gateway's own tmpfs volume; its log line ends with
   `ibkr=paper+live`. The VNC password is required because the Gateways read
   it at startup.
2. In `production.env`, set:
   - `FACTORLAB_IBKR_VPS_GATEWAYS=true`;
   - `IBKR_PAPER_USERNAME` and `IBKR_LIVE_USERNAME`;
   - `IBKR_GATEWAY_IMAGE`, pinned by digest (from
     `docker buildx imagetools inspect ghcr.io/gnzsnz/ib-gateway:stable`).

   Then **remove** the `IBKR_HOST_*` and `IBKR_PORT_*` lines. The daemon then
   defaults to `ibkr-gateway-paper:4004` and `ibkr-gateway-live:4003`.
3. Each Gateway needs about 1 GB of RAM (`IBKR_GATEWAY_MEM_LIMIT`,
   `IBKR_JAVA_HEAP_SIZE`); check `free -h`.
4. Releases start the Gateways with `up -d` and never force-recreate them, so an
   unchanged Gateway keeps its session. Changing the image pin or settings
   recreates the Gateway and needs a fresh login.
5. To watch the login or 2FA state, run
   `ssh -L 5900:127.0.0.1:5900 -L 5901:127.0.0.1:5901 ubuntu@145.239.75.163`
   and point a VNC viewer at `localhost:5900` (paper) or `localhost:5901`
   (live). Add `--profile ibkr-gateway` to the `fl` helper.
