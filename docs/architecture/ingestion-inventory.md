# Ingestion inventory

**Owner:** factorlab-pm · **Last updated:** 2026-06-16

> **Stale (2026-09-24):** Postgres references below predate the ClickHouse v2 cutover. The target ingestion model (provider adapters → engine → DB-service sinks) is [07-ingestion-provider-abstraction.md](07-ingestion-provider-abstraction.md); this inventory is refreshed in its phase P8.

Living index of every script/module that fetches, parses, or upserts data.

## Country × domain matrix

| Country | Domain                                                         | Vendors / sources                                                              |
|---------|----------------------------------------------------------------|--------------------------------------------------------------------------------|
| US      | equities                                                       | Schwab (REST + live), EODHD (daily)                                            |
| US      | broker mirror                                                  | IBKR (read-only positions / account / executions / open orders → `broker.*`)   |
| US      | political (alt-data)                                           | House Clerk · Senate eFD · Senate Stock Watcher · FEC · Congress.gov · LDA · USASpending · Finnhub-contracts |
| IN      | equities                                                       | Upstox (intraday + futures)                                                    |
| EU/APAC | equities                                                       | (future EODHD multi-market)                                                    |
| future  | alt-data                                                       | Reddit / Twitter / arXiv stubs (not active)                                    |

## Scripts (entrypoints)

| Path                                                     | Kind        | Cadence                                          |
|----------------------------------------------------------|-------------|--------------------------------------------------|
| `scripts/us/equities/schwab/us_equities_schwab_auth.py`                            | auth        | Manual / weekly (Schwab refresh)                 |
| `scripts/us/equities/schwab/us_equities_schwab_historical.py`                        | backfill    | Manual (Schwab historical)                       |
| `scripts/us/equities/schwab/us_equities_schwab_live.py`                            | live        | Hourly cron, 24/7 (1m catch-up, SP500)           |
| `scripts/us/equities/schwab/us_equities_schwab_eod.py`                             | daily       | Daily post-close cron (R3K, last 7d)             |
| `scripts/us/equities/blackrock/us_equities_blackrock_universe.py`                        | maintenance | Quarterly (BlackRock IWV → universe yamls)       |
| `scripts/us/equities/eodhd/us_equities_eodhd_daily.py`                           | daily       | EOD (EODHD)                                      |
| `scripts/us/ibkr/us_portfolio_ibkr_snapshot.py`                                    | daily       | Daemon, 06:00 + 16:30 New York (IBKR paper + live) |
| `scripts/us/political/us_political_backfill.py`                       | backfill    | Manual (4-phase)                                 |
| `scripts/us/political/us_political_daily.py --mode daily`            | daily       | Mon–Sat 17:30 IST                                |
| `scripts/us/political/us_political_daily.py --mode weekly`           | daily       | Sun 17:30 IST                                    |
| `scripts/us/political/us_political_daily.py --mode hourly`           | daily       | Hourly (self-healing retry)                      |
| `scripts/us/political/us_political_status.py`                         | maintenance | Manual                                           |
| `scripts/us/political/us_political_verify.py`                         | maintenance | Run as part of `--mode weekly`                   |
| `scripts/us/political/house_clerk/us_political_house_clerk_historical.py`    | backfill    | Manual (one-shot 2013–2026)                      |
| `scripts/us/political/_maintenance/us_political_refresh_bioguide.py`               | maintenance | Manual (post-matcher edit)                       |
| `scripts/us/political/_maintenance/*.py`                 | maintenance | All ad-hoc (cache cleanup, paper-PTR fills, ...) |
| `scripts/in/equities/upstox/india_equities_upstox_auth_server.py`                    | auth        | Railway always-on; Upstox OAuth callback         |
| `scripts/in/equities/upstox/india_equities_upstox_premarket.py`                      | premarket   | Daily 06:00 IST                                  |
| `scripts/in/equities/upstox/india_equities_upstox_live.py`                           | live        | Long-running 03:40–10:05 UTC                     |
| `scripts/_shared/factlab_backfill.py`                    | dispatcher  | Manual (one CLI for any Backfiller)              |
| `scripts/_shared/sync_raw_to_nas.py`                     | maintenance | Weekly (NAS backup mirror)                       |
| `scripts/_shared/notifier_daemon.py`                     | service     | At log-on (Outlook bridge)                       |

## Source modules

| Module                                                                | Subpackages                                                                                          |
|-----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| `factorlab.countries.us.equities.schwab`                              | `auth`, `candles`, `client`, `intraday`, `quotes`, `symbols`                                         |
| `factorlab.countries.us.equities.eodhd`                               | `client`, `candles`, `instruments`, `backfill`                                                       |
| `factorlab.countries.us.political`                                    | `_client`, `_state`, `_security`, `_db`, `_resolver`, `_bioguide`, `_asset_classifier`, `_metrics`, `orchestrator` |
| `factorlab.countries.us.political.house_clerk`                        | `client`, `index`, `parser`, `ingest`                                                                |
| `factorlab.countries.us.political.senate_efd`                         | `scraper`, `parser`, `ingest`                                                                        |
| `factorlab.countries.us.political.senate_stock_watcher`               | `ingest`                                                                                              |
| `factorlab.countries.us.political.fec`                                | `client`, `ingest`                                                                                    |
| `factorlab.countries.us.political.congress_gov`                       | `client`, `ingest`                                                                                    |
| `factorlab.countries.us.political.lda`                                | `client`, `ingest`                                                                                    |
| `factorlab.countries.us.political.usaspending`                        | `client`, `ingest`                                                                                    |
| `factorlab.countries.us.political.finnhub_contracts`                  | `ingest`                                                                                              |
| `factorlab.countries.us.political.legislators`                        | `yaml_fetcher`, `ingest`                                                                              |
| `factorlab.countries.in_.equities.upstox`                             | `auth`, `client`, `instruments`, `universes`                                                         |

## Shared plumbing every source uses

- `factorlab.shared.paths` — RAW_ROOT / STATE_ROOT / TOKEN_ROOT / LOG_ROOT helpers
- `factorlab.shared.runtime` — logging / signals / dedup / market / lock / state / heartbeat / supervised
- `factorlab.shared.notify` — Outlook + SMTP + Telegram + webhook + JSONL (one `notify()` entry)
- `factorlab.shared.ingest` — HTTPClient + State + Backfiller protocol + redact helpers

## Where data ends up

- **Canonical** (rows): Postgres on Railway. Schemas: `ref`, `market_us`, `market_in`, `universe`, `alt_political_us`, `audit`.
- **Canonical** (raw bytes): `<repo>/data/<source>/raw/`.
- **Backup mirror**: `E:\NAS\factorlab\raw\` — populated weekly by `sync_raw_to_nas.py`.
- **State checkpoints**: `<repo>/data/_state/` and `<repo>/data/<source>/_state/` (political legacy).
- **Logs**: `<repo>/logs/`.
- **Heartbeats**: `<repo>/data/_heartbeat/<service>` — mtime = liveness.
- **Tokens**: `<repo>/data/<vendor>/.token` — repo-local; never on NAS.

## Sister docs

- Per-country deep dives: `docs/countries/{us-equities,india-equities,...}.md`
- Per-vendor deep dives: `docs/data-sources/{01,04,07,...}-*.md`
- Operations: this folder
