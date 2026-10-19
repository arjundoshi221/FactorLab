# Configured US equity production collection

The `universe-us` service resolves the configured US equity roster, validates every
symbol through Schwab quotes, and publishes daily expected series. The checked-in
seed selects S&P 500, not all US listed stocks. The exact Russell 3000 CSV path is
described in [the activation runbook](../operations/us-russell3000-daily.md).
Canonical symbols use `BRK-B`; Schwab requests use `BRK/B`.

Schwab owns daily OHLCV for every published daily member and regular-session
one-minute candles for a separately capped tier ranked weekly by 20-session
median dollar volume. `US_MINUTE_TIER_SIZE` accepts 0-250 (default 250).
The engine providers remain shadow; production uses the component's legacy daemon.

Run `factorlab-ingest-us daemon --backfill` for resumable recovery, or add `--daemon`
for continuous operation. Both modes share `/app/data/us-ingest.lock`; concurrent
collectors exit without running. Production uses the `ingest-us` Compose service
with a 1 GB memory limit and restart policy `unless-stopped`.

Daily histories and updates use one price-history call per member; there is no
Schwab bulk daily endpoint. Runnable daily recovery takes priority over minute
collection. Partial and failed responses stay queued with per-symbol backoff.
Weekly full refreshes revisit adjusted history. Exchange sessions use XNYS and
America/New_York, including early closes and DST. Checkpoints follow candle and
coverage writes; latest-version reads deduplicate overlapping writes.

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

Rollout follows the per-component release workflow in
[`deploy/README.md`](../../deploy/README.md). Operator configuration is preserved
across releases; expanding membership requires installing and activating the roster.

Rollback follows the component's writer contract and release runbook. This change
does not alter the data contract or run schema migrations.
