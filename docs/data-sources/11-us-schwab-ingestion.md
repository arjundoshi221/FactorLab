# US listed-equity production collection

The production universe contains active USD common stocks, including listed
ADRs and foreign common shares, on Nasdaq, NYSE, and NYSE American. ETFs,
preferreds, warrants, units, notes, OTC securities, and derivatives are
excluded. Canonical symbols use `BRK-B`; Schwab requests use `BRK/B`.

EODHD owns the daily master and daily OHLCV for the full universe. Schwab owns
regular-session one-minute candles for a dynamic top-250 tier ranked weekly by
20-session median dollar volume.

Run `python scripts/factlab_us_clickhouse.py --backfill` for resumable one-shot recovery, or add `--daemon` for continuous operation. Both modes share `/app/data/us-ingest.lock`; concurrent collectors exit without running. Production uses the `ingest-us` Compose service with a 1 GB memory limit and restart policy `unless-stopped`.

The daemon refreshes the master daily, ingests the latest completed bulk daily
snapshot, records coverage for every expected symbol, and queues partial or
failed rows for per-symbol recovery. It recomputes the liquid minute tier weekly
and polls completed minute candles every five minutes during the regular
session. Exchange sessions use XNYS and America/New_York, including early closes
and DST. Each series commits its checkpoint only after candles and session
coverage are written; retries use latest-version reads to deduplicate overlapping
writes.

Scheduling is internal to this single long-running service; no separate US cron
entry is required. Provider authentication failures remain visible in source
health and are retried without a container restart loop.

Initial requests start in 1970 for daily data and sixty calendar days ago for minute data. Schwab can return a shorter available window. The preflight returned AAPL daily history from January 1985 and 34 recent minute sessions. Coverage begins at each series' first returned timestamp; unavailable older data is not invented. Missing bars inside available sessions remain visible and are retried while within retention. Daily and minute values retain the vendor's price basis; `adj_close` stays null because the response supplies no distinct adjusted-close field. Corporate actions and independently adjusted return series are outside this pilot.

Schwab authentication stays in the existing protected Cloudflare broker-auth flow. Only the rotating access token and expiry arrive in the US tmpfs volume. Missing, expired, or rejected tokens pause collection and appear in source status. Reauthenticate on the broker-auth page when required; no broker credentials are exposed through the research API or browser.

The private explorer is `/us`. `/hub/api/v1/us` serves the loopback-only browser hub. Bearer-protected `/api/v1/us` exposes:

- `dashboard?trading_date=YYYY-MM-DD`, `sources/status`, `instruments?search=AAPL`
- `candles/1min` and `candles/daily` with symbol, date_from, date_to, limit and cursor
- `instruments/{instrument_id}/days` with an optional date range, up to 366 days
- `ingestion/runs` with limit and offset

Candle cursors are bound to the resolution and filters. Dates denote New York sessions; minute response timestamps are UTC. One page contains at most 1,000 candles. Minute queries span at most 366 days. Session checks distinguish requested/available coverage from collection timestamps. A stale heartbeat means the collector has not reported for ten minutes, including outside trading hours.

For rollout, upload the reviewed image archive and Compose file to the release directory used by `deploy/scripts/deploy-us.sh`. The script backs up server configuration, applies only the additive US DDL, runs initial recovery, then starts US collection and updates the API with `--no-deps`. India, political, and secret-agent images remain unchanged. Run `python scripts/verify_us_deployment.py` inside the API container afterward. Open-session polling must be verified during the next trading session when deploying on a closure.

Rollback: stop `ingest-us`, restore the backed-up Compose and production.env files, and recreate only `api` with `--no-deps`. Keep the additive tables and collected data. Do not run a global Compose down/up.
