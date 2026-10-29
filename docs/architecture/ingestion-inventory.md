# Ingestion inventory

**Owner:** factorlab-pm · **Last updated:** 2026-10-30

This page lists what each running ingestion service collects: its universe, resolution and cadence.

- **Declared values** live on each pipeline in
  [catalog_pipelines.py](../../components/api/src/factorlab/components/api/catalog_pipelines.py)
  (`service`, `universe`, `resolution`, `schedule`).
- **Live values** are in the hub: **Monitoring → Collection plan**
  (`GET /hub/api/v1/monitoring/collection-plan`, [F-045](../features/F-045-collection-plan.md)).
  That view shows the universe each latest run recorded, its active members, the observed gap
  between runs, and the config each collector reports (`meta.ingestion_runs.metadata_json["config"]`).
  It flags drift from the values below.

The ingestion model (provider adapters → engine → DB-service sinks) is
[07-ingestion-provider-abstraction.md](07-ingestion-provider-abstraction.md). Bindings are in
[configs/ingestion/bindings.yaml](../../configs/ingestion/bindings.yaml).

## Per service

| Component / service | Pipeline(s) | Universe (`universe_id`) | Resolution | Cadence | Set by |
|---|---|---|---|---|---|
| ingest-us / `universe-us` | `us_universe_sync`, `us_shares_outstanding` | `us_configured`: iShares IWV Russell 3000 proxy (`r3k_iwv`, ~2,580 names, minimum 2,400) plus 30 benchmark symbols (`$SPX`, sector SPDRs, RSP*, IWV) | membership | every `refresh_interval_minutes` (1440); retry 15 min | `/etc/factorlab/us-universe.yaml` (seed [deploy/us-universe.yaml](../../deploy/us-universe.yaml)) |
| ingest-us / `ingest-us` | `us_live` | `us_liquid_250` (the minute tier, up to `US_MINUTE_TIER_SIZE` = 250 of the published names) | 1min | sweep every `US_LIVE_INTERVAL_SECONDS` (300 s), XNYS regular session | env, `collector.py` |
| | `us_recovery_daily` | `us_configured` (every published member) | daily | continuous until complete, retry backoff from 15 min; weekly full refresh | published membership |
| | `us_recovery_1min` | `us_liquid_250` | 1min, last 60 days | continuous, retry backoff from 15 min | env |
| ingest-us / `indexes-us` | `us_index_compute` | sector and composite indexes over the universe | daily | weekdays after 23:30 UTC, trailing 14 days | [catalog_us.yaml](../../libs/indexes/src/factorlab/indexes/catalog_us.yaml) |
| ingest-us / `futures-us` | `us_futures_roster`, `us_futures_quotes`, `us_futures_daily` | `us_futures`: 14 roots, ~72 contracts | quotes; daily bars | quotes every `--interval` (60 s) while Globex is open; roster once per session; provisional bar at 17:00 ET, settled an hour into the next session | [us_futures.yaml](../../configs/universes/us_futures.yaml) |
| ingest-india / `ingest-india` | `india_intraday_1min`, `india_historical_1min_recovery`, `india_reference_premarket` | `UPSTOX_UNIVERSE` (default `full_nse_eq`) plus nearest stock futures | 1min | 03:45–10:02 UTC on NSE days. Full universe: quotes every minute (+5 s). Named universe: every 300 s, with recovery. Futures every 600 s. | `production.env`, [india.yaml](../../configs/universes/india.yaml) |
| ingest-political / `ingest-political` | `political_bootstrap` | `house` (newest `POLITICAL_PTR_LIMIT` = 20 PTRs) | filings | daily 02:15 UTC (host cron) | `production.env` |
| ingest-broker / `ibkr-snapshot` | `ibkr_broker_snapshot` | `ibkr_accounts` (`IBKR_SNAPSHOT_MODES` = paper, live) | snapshot | `IBKR_SNAPSHOT_TIMES` New York slots on XNYS days (production: every hour) | `production.env` |

## Keeping it current

- Every pipeline id a collector passes to `ingestion_run` needs a catalogue entry that has a `service`.
  [test_collection_plan.py](../../components/api/tests/test_collection_plan.py) fails when an
  ingestion service in the manifests has no pipeline.
- Record a new collector setting in its `config=run_config(...)` call (`factorlab.ingest`). The hub
  then shows the production value, not the repository default.
- Change the table above when a declared universe or cadence changes.

## Where data ends up

- Rows go to ClickHouse v2 ([06-schema-rehau.md](06-schema-rehau.md)).
- Every provider response is archived to `raw.archive`.
- Run records are in `meta.ingestion_runs`, and the expected inventory is in `meta.expected_series`.
- Logs are under `/var/log/factorlab/<component>/<service>.jsonl` ([log-access.md](../operations/log-access.md)).
