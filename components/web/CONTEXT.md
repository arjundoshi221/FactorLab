# web

> The hub UI (React + Vite) as static files behind non-root nginx on 127.0.0.1:8080.

## Purpose

`web` is the FactorLab Data Hub single-page app: data catalog, schema explorer, pipelines,
India / US / political dashboards and the Docker images page. It is a `static` component:
the image `ghcr.io/arjundoshi221/factorlab-web` holds only the built bundle and nginx; all
data comes from the `api` component's `/hub/api/v1/*` routes on the same hostname.

## Owns and does not own

- Owns: everything under [src/](src/) (pages, components, shared fetch helpers, the TypeScript response types), [nginx/nginx.conf](nginx/nginx.conf), the [Dockerfile](Dockerfile) and the compose fragment [deploy/compose.yaml](deploy/compose.yaml).
- Does not own: the JSON API and its models (`api`), edge routing and the Access application (Cloudflare dashboard, see [deploy/edge/README.md](../../deploy/edge/README.md)), the compose base and the `edge` network (`platform`).

## Entry points

- Compose service `web`, profile `web`, network `edge` only (it cannot reach ClickHouse or the writers), published on `127.0.0.1:8080`, `read_only`, `mem_limit: 64m`, user 101.
- nginx: `GET /healthz` returns `{"status":"ok"}`; `/assets/` is cached immutable; every other path falls back to `index.html` (`no-cache`); `^/(hub/api|api)/` and
  `/health`, `/docs`, `/redoc`, `/openapi.json` return 404 so a misrouted API call never
  gets the SPA. `/version.json` carries `{"component":"web","version","commit"}`.
- Client routes (in [App.tsx](src/App.tsx)): `/`, `/data`, `/data/tables/<db>.<table>`,
  `/data/pipelines`, `/schema`, `/schema/v2`, `/india`, `/india/instruments/<uuid>`, `/us`, `/market-data`,
  `/political`, `/docker-images`, `/roadmap`, `/monitoring`, `/portfolio[/trades|/themes|/strategies|/trade-names]`; `/market-data?view=matrix` is the data matrix, `?view=sectors` the by-sector view and `?view=indexes` the indexes dashboard (`/indexes` redirects there) and `?view=futures` the futures view (`/futures` redirects there).
- npm scripts ([package.json](package.json)): `dev` (Vite on 127.0.0.1, proxies `/hub/api`
  to `http://127.0.0.1:8000`), `build` (`tsc -b && vite build`), `test` (`vitest run`).

## Configuration and secrets

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| Build version | `VITE_FACTORLAB_VERSION` | `0.0.0-dev` (shown as none) | set from the `VERSION` build arg |
| Build commit | `VITE_FACTORLAB_COMMIT` | `unknown` | set from the `COMMIT` build arg |
| Enable the service | `FACTORLAB_WEB_ENABLED` (in `production.env`) | off | turns on the `web` compose profile |
| Image pin | `FACTORLAB_WEB_IMAGE` | `ghcr.io/arjundoshi221/factorlab-web:unreleased` | written by the deployer to `state/images.env` |

No secrets (`secrets: []`). The browser never sends credentials of its own; Cloudflare
Access cookies do the authentication.

## Data

**Portfolios:** `/portfolio` shows account summary cards, orders beside holdings, on-demand three-tag editors and collapsed activity. `/portfolio/themes`, `/portfolio/strategies`, `/portfolio/trade-names` manage profile catalogs and cumulative FIFO stock/ETF PnL in expandable comparison tables; currencies/modes/simulations remain separate with explicit unavailable/stale states. [Feature and UI behavior](../../docs/features/F-036-portfolio-tagging-pnl.md). Profile/account changes clear report data; refreshes preserve drafts and last results. Search/status filters cover the current 50-order page. Navigation preserves profile/account context; all testers share access. Each `TagSelectors` dropdown ends with "+ New <kind>…", which swaps in a name field, POSTs the catalog entry, selects it and refreshes that catalog. `PortfolioUI.tsx` owns shared controls and exact decimal-string formatting; `Portfolio.css` scopes responsive styles. Headline balances use only account-wide metrics in the base currency; unavailable values remain em dashes. **Trade ledger** (F-040): `/portfolio/trades` (`TradeLedger.tsx`) lists every executed order across the profile's accounts with theme/strategy/trade name; filters (account, ticker, listing, tags, tag status, dates) are URL-backed and applied by the API across full history, with status chips showing counts. Inline and bulk tagging reuse `TagSelectors` and the existing assignment PUT (two at a time, at most 200 selected; failures stay selected and are listed). Rows link to the ticker; account pages link to their trades. [F-040](../../docs/features/F-040-trade-ledger.md).

The Docker images page explains the component/service/image relationship, inventory scope,
tag/digest/ID differences, and timestamps (IST). Expandable details expose full identifiers,
allowlisted build labels, container references, service start times and pins, and monolith
release history metadata. Monolith records are distinguished from independent component
deployments. Image sizes count shared layers per image and are not disk usage or reclaimable
space; stopped container references are distinguished from unreferenced images.

None in ClickHouse. It calls, among others, `/hub/api/v1/overview`, `/hub/api/v1/catalog*`,
`/hub/api/v1/schema-map`, `/hub/api/v1/docker-images`, `/hub/api/v1/us/*`,
`/hub/api/v1/india/*`, `/hub/api/v1/market-data/*`, `/hub/api/v1/political/*`. Host path:
`/var/log/factorlab/web` (bind mount for nginx logs).

## Dependencies and contracts

- Runtime: `react`, `react-dom`, `@xyflow/react`, `elkjs`. Dev: Vite 7, TypeScript,
  Vitest 4 (patched mocker), Testing Library, jsdom. Images: `node:22-bookworm-slim` (build) and
  `nginxinc/nginx-unprivileged:1.27-alpine` (runtime).
- Contract: the TypeScript types (`dataTypes.ts`, `indiaTypes.ts`, `usTypes.ts`,
  `politicalTypes.ts`, `schemaTypes.ts`) mirror the `api` response models; change both
  together. Errors surface the API's `detail` string ([shared/api.ts](src/shared/api.ts)).
- Edge contract: tunnel rule 1 sends `^/((hub/api|api)/|(health|docs|redoc|openapi\.json)$)`
  to `:8000`; the catch-all goes to `:8080` ([deploy/edge/README.md](../../deploy/edge/README.md)).

## Observability

- Logs: `/var/log/factorlab/web/access.jsonl` (JSON lines in the Python log shape:
  `component=web`, `service=web`, `logger=nginx.access`, `method`, `route` (no query
  string), `status`, `bytes`, `duration_s`, `request_id` = `cf-ray`) and
  `/var/log/factorlab/web/error.log`; errors also go to Docker's log.
- Read with the `factorlab-logs` skill: `uv run python tools/read_logs.py tail --component web`.
- Health: image `HEALTHCHECK` fetches `http://127.0.0.1:8080/healthz`; the deployer waits
  for Docker `healthy`.

## Tests

Vitest specs sit next to the code (`src/**/*.test.ts(x)`, setup in `src/testSetup.ts`):

```bash
cd components/web && npm ci && npm test && npm run build
```

## Release and rollback

`.\deploy\release.ps1 -Component web [-Bump ...] [-DryRun]` writes the version into
`package.json` and `package-lock.json` (a `static` unit) and pushes `web/vX.Y.Z`.
[component-release.yml](../../.github/workflows/component-release.yml) runs `npm ci`,
`npm test`, `npm run build`, builds and verifies the image (label and user checks), then
`deploy-component.sh web ...` via [_deploy.yml](../../.github/workflows/_deploy.yml).
Rollback class `auto` (restore the previous fragment and pin on failed verification);
manual rollback with [component-rollback.yml](../../.github/workflows/component-rollback.yml).
Rolling the UI back at the edge means pointing the tunnel catch-all at `:8000` again,
which works only while the API runs the monolith image (the legacy `release/*` path that
still runs production until the rollout completes).

## Pitfalls

- **The hub has no in-app login.** Cloudflare Access via the tunnel is its only
  authentication. Never publish `:8080` beyond `127.0.0.1` or route to it except through
  the tunnel.
- Enable `FACTORLAB_WEB_ENABLED=true` before the first release: the manifest does not mark
  `web` as profile-gated, so the deployer's preflight expects the service in the model.
- The CSP is `default-src 'self'` (inline styles allowed): no third-party scripts, fonts
  or API origins without changing both `nginx.conf` blocks.
- Security headers are repeated in `location /` because nginx drops inherited
  `add_header` when a location sets its own.
- `access.jsonl` is rotated with `copytruncate` (nginx keeps the file open); a few lines
  can be lost at rotation.
- `/india/instruments/<id>` expects a UUID; `/data/tables/<name>` must match
  `^[a-z_]+\.[a-z0-9_]+$`, as on the API side.

**UDP service monitoring:** The primary navigation has a visible Monitoring tab at `/monitoring`. Its responsive health overview and service cards show human-readable statuses, last reports, instance uptime and restart counts, with delayed/missing heartbeats first. Scheduled jobs awaiting telemetry explain that their first monitored run has not reported. Selecting a card filters the latest 100 events; service, event type and relative time range filters are applied by the API. Polling runs every 15 seconds with manual refresh, loading states and a checked timestamp. Failed refreshes retain the last successful snapshot; a stale watcher shows the monitor offline banner. Page-specific styles live in `src/pages/Monitoring.css`. **Status verdict:** the page opens with `MonitoringStatus` (`/monitoring/summary`, 30s poll, also refreshed by "Refresh now"): a verdict, problems worst first with links to their evidence, collapsed notes and passing checks. It replaces the summary tiles and top source banners; the section nav is sticky; service/pipeline/coverage cards carry `#service-<id>`, `#pipeline-<id>` and `#coverage-<country>-<source>-<res>` anchors. Source problem/stale/service fields come from the API. [F-038](../../docs/features/F-038-monitoring-status-verdict.md).

The operations grid adds name/ID search and an attention filter (including unregistered senders, unknown telemetry and errors/warnings in the last hour). Cards display hourly error/warning counts, packet gaps, configured deadlines and seven UTC calendar days of stored telemetry from `/monitoring/daily`. Activity strips are not uptime measurements. Daily ingestion cards reuse `/catalog/pipelines`, with rolling 24-hour runs/rows/problems, schedule-aware status, seven-day activity and expandable failed/partial run details. `/monitoring/coverage` supplies latest recorded feed sessions, missing bars, affected series and active listing series with no coverage record. A feed without checks remains visible as unknown coverage. Percentages apply only to recorded checks, not unreported series or entire missing sessions. Coverage/daily/pipeline requests refresh independently every 15s; manual refresh refreshes all, and failed panels retain explicitly stale snapshots. Pipeline data can be cached by the API for 60s. Shared operations UI and polling live in `src/pages/MonitoringOperations.tsx`.

**Collection coverage:** `/market-data?view=collection` switches between US and India and between recent daily collection (last 30 calendar days) and full historical recovery. Both views use the current `meta.expected_series` configuration; a ticker can appear in both because recovery
and daily collection write the same bars. The page searches configured symbols and company
names through `/hub/api/v1/market-data/universe`, shows zero-data tickers, and opens per-ticker
stored coverage and recovery state. List requests are paginated at 50 tickers.
Indian equity and futures series are separate rows even when they share a listing ID.
Selecting a row opens a focused side panel with the provider-to-FactorLab ticker mapping, coverage and recovery details. Escape and the close control return to the universe list. Search accepts either ticker, as well as company names. Future rows label the FactorLab ticker as the underlying and retain their separate contract ID.

**Collection health**: Whether a source is successfully doing its collection work.
It is independent of process heartbeat liveness; a reported source failure remains
prominent even when that service reports on time.

**Error inbox**: A filtered history of grouped service errors/warnings, failed/partial
ingestion runs and current source problems. Its first-seen time and occurrence count
describe the selected history window; a current source status has no invented start
time or occurrence count.

**Recovery evidence**: A successful run of the same pipeline and provider after the
last grouped failure. It supports investigation but does not establish that all
missing data has been repaired; a heartbeat alone supplies no recovery evidence.

**Market Data layout:** one page header, view tabs (All tickers · By sector · Indexes · Futures · Data matrix · Collection coverage) and a shared filter toolbar; shared `md-*` styles live in `src/pages/InstrumentExplorer.css` (prefixed to avoid the global `.instrument-*` India styles). The instrument detail has a Close control that clears `kind`/`id`; on phones the matrix and raw record grids stay scrolling tables instead of the global card layout.

**Instrument explorer:** **All tickers** is the default reference inventory, including inactive/empty listings and contracts. **Collection coverage** retains configured daily/history views. **Direct match** uses the selected canonical ID; security/issuer/underlying context stays separate. **Instrument coverage** means scoped lifetime stored counts, not collection intent. **Investigation candidates** have supporting alias scope/validity but never establish confirmed coverage. **Recorded provenance** follows raw/run/computation IDs; absent evidence remains unavailable. Filters and selection are shareable. [Specification and API contracts](../../docs/features/F-037-instrument-explorer.md). **Data matrix** (F-039): `/market-data?view=matrix` (`DataMatrix.tsx`) shows each ticker against bars per configured feed, futures, issuer-level fundamentals, political trades, holdings, trades and tags. States: up to date, behind, missing, stored (not configured), none, needs tags, unavailable ("?"; never healthy). URL-backed search/country/scope/attention filters; tickers open the F-037 detail, which now starts with an "At a glance" strip; trade/tag cells open the ledger; dataset panels carry `#dataset-<table>` anchors. [F-039](../../docs/features/F-039-ticker-data-matrix.md).

**By sector:** `/market-data?view=sectors` groups listings (not contracts) by current GICS sector from `/market-data/sectors`, with an Unclassified group; the open sector is URL-backed (`sector=<id|none>`) and lazily loads `/instruments?kind=listing&sector=`. The shared `InstrumentTable` (also used by All tickers) shows each instrument's sector.

**Indexes (F-042):** the Market Data **Indexes** tab (`?view=indexes`; `/indexes` redirects there and is no longer in the operations nav) renders `<Indexes embedded />` ([Indexes.tsx](src/pages/Indexes.tsx), `indexTypes.ts`, `Indexes.css`; `embedded` drops its own hero): tracked-index strip; sector heatmap (diverging blue/red, ink text) with cap/equal toggle and ETF/S&P benchmark rows; hand-rolled SVG comparison chart rebased to 100 with crosshair/tooltip (dark categorical slots validated against `#12161b`, ETFs dashed); contributors and largest constituents.

**Futures (F-044):** `/market-data?view=futures` ([Futures.tsx](src/pages/Futures.tsx), `futuresTypes.ts`, `Futures.css`; chart primitives shared from `Indexes.css`) polls `/hub/api/v1/us/futures` every 60s. URL-backed group tabs (`group=index|energy|metals|rates|fx`) and root chips (`root=/ES`); the selected root shows expiry buttons (`contract=/ESZ26`, default the front traded contract) and range buttons (`range=1d|5d|1m|3m|1y|max`, default `5d`) over a hand-rolled SVG time series of that one contract from `/us/futures/{contract_id}/series` (points at their index so closed sessions leave no gaps; blue when the range ends up, red when down; 1D draws the previous settlement as a dashed reference; intraday ranges poll every 60s), a Contango/Backwardation/Flat label from front to back, and the contract table (symbols also select the charted contract; last, bid/ask, change vs previous settlement, volume, OI, quote age; stale rows muted). Failed refreshes keep the last snapshot. [F-044](../../docs/features/F-044-futures-curve-tab.md).

**Collection plan (F-045):** [MonitoringCollection.tsx](src/pages/MonitoringCollection.tsx) is the Monitoring page's "Collection plan" section (nav anchor `#monitoring-collection-title`, row anchors `#collection-<pipeline>`). It shows one table per component from `/hub/api/v1/monitoring/collection-plan`, refreshes every 60 s, and names unavailable observations instead of showing empty columns as clean.
