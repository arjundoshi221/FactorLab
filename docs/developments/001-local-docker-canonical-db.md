# 001 — Local Docker as canonical database; Railway as auth-only

> Status: `[accepted]` — decided 2026-05-08. Implementation pending.

## Decision

1. The canonical database is a **local Docker TimescaleDB container** running on the research workstation. There is no cloud database, anywhere, ever.
2. **Railway is the OAuth auth server only.** It runs `scripts/in/equities/upstox/india_equities_upstox_auth_server.py` (Flask + gunicorn) and handles callback URLs for every vendor that requires public-HTTPS OAuth. It holds no business data.
3. Cold backups go to **`E:\market_archive\`** (external drive). Optionally a second cold copy goes to cloud object storage (Backblaze B2 / S3 Glacier) of the same dump files — *not* a cloud database.

## Architecture

```
Local PC                              Railway
─────────────────                    ─────────────
TimescaleDB (Docker)        ←        Auth server only (Flask + gunicorn)
All ingestion                         ├─ /upstox/callback   (existing)
All processing                        ├─ /schwab/callback   (planned)
DBeaver / research                    └─ /<future-source>/callback
                                      Stateless. Tokens flow through, are
                                      written to a token file, and pulled
                                      to local via X-Auth-Pin header.

E:\market_archive\         ←         pg_dump weekly + parquet exports
                                      Cold storage. Two-copy minimum.
```

### Single-container Docker layout

`docker-compose.yml` already defines this. Only one service.

| Property | Value |
|---|---|
| Image | `timescale/timescaledb:latest-pg16` |
| Container | `factorlab-db` |
| Port | `127.0.0.1:5432` |
| Volume | named volume `pgdata` (WSL2-managed, internal SSD) |
| Restart | `unless-stopped` (starts on Windows boot) |

**Do not bind-mount the live volume to `C:\market_data\` or `E:\`.** Named volumes in WSL2 are faster on Windows and avoid OneDrive sync corruption on running DB files. The HDD is for backups, never for live IO.

### Storage partition

| Tier | Location | Purpose | IO profile |
|---|---|---|---|
| Live | Internal SSD (Docker volume) | working DB | random IO, low latency |
| Cold #1 | `E:\market_archive\postgres_backups\` | weekly `pg_dump` | sequential, infrequent |
| Cold #1 | `E:\market_archive\parquet\` | nightly hot-table parquet exports | sequential |
| Cold #1 | `E:\market_archive\raw\` | mirror of `data/<source>/raw/` | sequential |
| Cold #2 (recommended) | Cloud object store (B2 / S3 Glacier) | offsite copy of dump files | sequential, monthly |

### What runs where

| Component | Host | Why |
|---|---|---|
| TimescaleDB | Local Docker | canonical, low latency, free |
| All ingest scripts (Schwab, EODHD, Upstox, political) | Host conda env | OS-level deps, OAuth tokens, scheduler integration |
| IBKR `ib_async` | Host | requires TWS/Gateway running |
| Senate eFD scraper | Host | residential IP required (Akamai blocks cloud) |
| Backup script | Host (PowerShell) | `docker exec ... pg_dump` |
| Auth server | Railway | needs public HTTPS callback URLs |
| DBeaver | Host | connects to localhost:5432 |

## Rationale

| Dimension | Local Docker | (Rejected) Cloud DB |
|---|---|---|
| $/mo | $0 | $20–40 |
| Write latency | <1 ms loopback | 50–150 ms transatlantic |
| Backfill speed | Bound by CPU/disk | Bound by network round-trip — empirically dominant for large loads |
| Ownership | Full | Vendor-dependent |
| Availability | PC-uptime constrained | 24/7 |

The single real cost is "PC must be on when crons fire". For US market data this is non-trivial — see [002-live-us-market-data.md](002-live-us-market-data.md) — but solvable with sleep-policy + UPS, both required for live streaming anyway.

## What this deliberately *does not* include

- **No QuestDB.** TimescaleDB hypertables already cover time-series. A second engine adds writers, schemas, and backups for no incremental capability at our current data granularity (1-min bars, not ticks).
- **No pgAdmin.** DBeaver is the admin client. One UI is enough.
- **No `C:\market_data\` bind mount.** Named volume in WSL2 is faster and avoids OneDrive sync risk.
- **No live volume mirroring** (`robocopy` of the running `pgdata` directory). WAL state corrupts. Always use `pg_dump`.
- **No Railway DB**, ever. Not as primary, not as staging, not as fallback. Even when "live US data overnight" tempts a cloud worker, the answer is local + UPS, not Railway DB.
- **No second container for ingest.** Host conda env. Avoids Docker networking and credential plumbing.

## Auth pattern (locked, applies to every OAuth source)

Mirrors the existing Upstox flow. Documented here so it's not re-invented per vendor.

1. Vendor OAuth `redirect_uri` points to **Railway**, e.g. `https://<railway-app>/<vendor>/callback`.
2. Railway server exchanges code → token, writes to its own filesystem.
3. Local script calls Railway via `GET /<vendor>/token` with `X-Auth-Pin` header.
4. Local script caches token to `data/<vendor>/.token`.
5. All actual API calls happen from the host using that token.

Vendors planned for this pattern: Upstox (live), Schwab (planned), IBKR Web API (if adopted), any future broker/data vendor requiring OAuth callback URL.

`https://127.0.0.1` redirects are **not** used even when a vendor allows them — keeps the auth pattern uniform and lets us roll keys / deploy server changes in one place.

## Disaster recovery

With local-only and no cloud DB, backup discipline is the single safety net.

| Failure mode | Covered by |
|---|---|
| DB corruption / accidental DROP | weekly `pg_dump` to `E:\` |
| Internal SSD failure | weekly `pg_dump` to `E:\` |
| External HDD failure | Cold #2 cloud copy (recommended) |
| House fire / theft | Cold #2 cloud copy (recommended) |
| Ransomware on host | Cold #2 with versioning + immutability flag |

Cold #2 is ~10 GB compressed in cloud object storage. At Backblaze B2 = ~$0.05/mo. This is cheap insurance against the only catastrophic scenario this architecture is otherwise exposed to.

## Migration plan

Pending: confirmation of where the canonical DB currently lives. Three possibilities:

1. **Local Docker, currently stopped** → `docker compose up -d` + `docker volume ls` confirms data is intact. Done.
2. **Native Postgres on host** → `pg_dump` from native instance, `pg_restore` into Docker, point `DATABASE_URL` to container, decommission native.
3. **Other host (e.g., a previously-running Railway DB)** → one-shot `pg_dump` over network, restore to Docker, decommission source.

Once confirmed, the migration script and verification queries (row counts on `alt_political_us.legislator_trades`, `alt_political_us.gov_contracts`, `alt_political_us.campaign_donations`, `market_us.candles_*`) go in `scripts/factlab_db_migrate.py`.

## Files affected (when implemented)

- `docker-compose.yml` — already correct, no change needed
- [src/factorlab/storage/db.py:21](../../src/factorlab/storage/db.py#L21) — `DATABASE_URL` continues to read from `.env`; flip to local URL post-migration
- `.env` — `DATABASE_URL` → `postgresql+psycopg2://factorlab:...@127.0.0.1:5432/factorlab`
- `scripts/factlab_backup_pg.ps1` — new, weekly Task Scheduler job
- `scripts/factlab_db_migrate.py` — new, one-shot migration
- [docs/architecture/database.md](../architecture/database.md) — update from "Postgres on Railway" to "Local Docker TimescaleDB"
- [docs/README.md](../README.md) — update structure section to reflect new docs
- `scripts/in/equities/upstox/india_equities_upstox_auth_server.py` — add `/schwab/callback` route and `/schwab/token` fetch route

## Open questions

- **Where does the canonical DB live right now?** Determines migration path. Memory says alt_political is live with 2.4M+ rows; Docker shows no running container. Must confirm before any migration step.
- **Cold #2 cloud copy: yes or no?** Default-yes recommendation; explicit decision owed.
- **Backup retention policy.** Default proposal: 12 weekly + 12 monthly + yearly forever. Explicit decision owed.
