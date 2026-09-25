# FactorLab API reference [alpha]

## Overview

FactorLab exposes private read APIs and narrowly scoped Hub UI-state writes backed by ClickHouse.

At the v2 cutover, the existing `/api/v1` and `/hub/api/v1` paths keep their
URLs but use canonical `listing_id`, nullable `contract_id`, and (for political
trades) `political_trade_id`. Clients must restart pagination: v1 cursors are
rejected. Daily bars expose raw OHLCV; adjusted close is unavailable until an
adjusted-bars dataset is built.

```text
SSH-tunnel base URL: http://127.0.0.1:8000
Web dashboard:       http://127.0.0.1:8000/
India Markets:       http://127.0.0.1:8000/india
Schema map:          http://127.0.0.1:8000/schema
Interactive docs:    http://127.0.0.1:8000/docs
OpenAPI schema:      http://127.0.0.1:8000/openapi.json
```

The production service binds only to VPS loopback. Open an SSH tunnel before
using the website or API. A Cloudflare Access URL will replace the tunnel after
a domain is connected.

## Authentication

`GET /health` and the loopback-only web hub routes do not require a bearer
token. All existing `/api/v1/...` data endpoints require the shared
`FACTORLAB_API_KEY`:

```http
Authorization: Bearer <FACTORLAB_API_KEY>
```

On the VPS, load the key into a temporary shell variable without printing it:

```bash
FACTORLAB_KEY=$(sudo docker exec factorlab-api-1 \
  cat /run/secrets/app/FACTORLAB_API_KEY)
```

Do not commit, log, or paste this value into documentation or chat.

For access from another computer, prefer an SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@145.239.75.163
```

Then use `http://127.0.0.1:8000` as the base URL.

## Endpoint summary

| Method | Path | Authentication | Purpose |
|---|---|---|---|
| `GET` | `/` | SSH/edge boundary | FactorLab Data Hub dashboard |
| `GET` | `/india` | SSH/edge boundary | India instrument and trading-session explorer |
| `GET` | `/schema` | SSH/edge boundary | Schema explorer: v2 areas, tables, columns, and links in plain language |
| `GET` | `/roadmap` | SSH/edge boundary | V1-V5 product roadmap |
| `GET` | `/hub/api/v1/overview` | SSH/edge boundary | Cached table inventory and schedule-aware health |
| `GET` | `/hub/api/v1/docker-images` | SSH/edge boundary | Host Docker image inventory snapshot |
| `GET` | `/hub/api/v1/schema-map` | SSH/edge boundary | v2 areas, tables, columns with descriptions, keys, and categorized logical links |
| `GET` | `/hub/api/v1/india/dashboard` | SSH/edge boundary | Selected-day India collection health |
| `GET` | `/hub/api/v1/india/instruments` | SSH/edge boundary | Unique instruments with coverage and value checks |
| `GET` | `/hub/api/v1/india/instruments/{id}/days` | SSH/edge boundary | Exchange-session checks for one instrument |
| `GET` | `/health` | No | API liveness check |
| `GET` | `/api/v1/india/candles/1min` | Bearer | Indian one-minute OHLCV/OI candles |
| `GET` | `/api/v1/india/instruments` | Bearer | Search and page through Indian reference instruments |
| `GET` | `/api/v1/india/stats` | Bearer | Overall Indian instrument and candle coverage |
| `GET` | `/api/v1/india/stats/daily` | Bearer | Candle coverage grouped by Indian trading date |
| `GET` | `/api/v1/india/dashboard` | Bearer | Today/historical collection dashboard summary |
| `GET` | `/api/v1/india/coverage` | Bearer | Expected versus actual coverage by series |
| `GET` | `/api/v1/india/collection/activity` | Bearer | Data physically collected on an ingestion date |
| `GET` | `/api/v1/india/freshness` | Bearer | Live, stale, and never-seen expected series |
| `GET` | `/api/v1/india/gaps` | Bearer | Contiguous missing one-minute ranges |
| `GET` | `/api/v1/india/anomalies` | Bearer | Data-quality and collection anomalies |
| `GET` | `/api/v1/india/metrics/timeseries` | Bearer | Dashboard-ready hourly or daily metrics |
| `GET` | `/api/v1/india/instruments/{id}/summary` | Bearer | Instrument metadata and data-health drill-down |
| `GET` | `/api/v1/india/ingestion/runs` | Bearer | Ingestion run history |
| `GET` | `/api/v1/india/ingestion/runs/{id}` | Bearer | One ingestion run |
| `GET` | `/api/v1/india/sources/status` | Bearer | Latest health of every source pipeline |
| `GET` | `/api/v1/us/dashboard` | Bearer | US daily and minute coverage summary |
| `GET` | `/api/v1/us/instruments` | Bearer | Canonical US listings and recovery state |
| `GET` | `/api/v1/us/candles/{resolution}` | Bearer | `daily` or `1min` raw bars |
| `GET` | `/api/v1/us/instruments/{listing_id}/days` | Bearer | US session coverage for a listing |
| `GET` | `/api/v1/us/ingestion/runs` | Bearer | US ingestion run history |
| `GET` | `/api/v1/political/trades` | Bearer | US congressional transaction disclosures |
| `GET` | `/api/v1/political/dashboard` | Bearer | Political collection and quality overview |
| `GET` | `/api/v1/political/coverage` | Bearer | Filing parse and entity-resolution coverage |
| `GET` | `/api/v1/political/collection/activity` | Bearer | Filing/trade rows collected on a UTC date |
| `GET` | `/api/v1/political/freshness` | Bearer | Freshness by political dataset and source |
| `GET` | `/api/v1/political/anomalies` | Bearer | Disclosure completeness and quality findings |
| `GET` | `/api/v1/political/metrics/timeseries` | Bearer | Political activity and quality metrics |
| `GET` | `/api/v1/political/legislators` | Bearer | Search canonical legislators |
| `GET` | `/api/v1/political/legislators/{id}/summary` | Bearer | Legislator disclosure drill-down |
| `GET` | `/api/v1/political/tickers` | Bearer | Unique disclosed tickers and totals |
| `GET` | `/api/v1/political/tickers/{ticker}/summary` | Bearer | Ticker political-activity drill-down |
| `GET` | `/api/v1/political/ingestion/runs` | Bearer | Political ingestion history |
| `GET` | `/api/v1/political/ingestion/runs/{id}` | Bearer | One political ingestion run |
| `GET` | `/api/v1/political/sources/status` | Bearer | Political pipeline health |
| `GET` | `/docs` | No | Swagger interactive documentation |
| `GET` | `/openapi.json` | No | Machine-readable OpenAPI schema |

## Health

### Web hub overview

`GET /hub/api/v1/overview` discovers tables in the v2 `raw`, `ref`, `market`,
`meta`, and `alt` databases (and the other installed v2 namespaces),
including empty tables. Counts come from active ClickHouse parts and are
reported as fast stored-row counts rather than deduplicated `FINAL` counts.
Known tables also include their domain date range, latest ingestion, rows for
today, and a schedule-aware status. The response is cached for 55 seconds.

`GET /hub/api/v1/docker-images` reads the host collector's atomic JSON
snapshot. It returns `snapshot_at`, `release_id`, `stale`, and every locally
stored image with its ID, tags, digests, size in bytes, creation time,
associated container names/status/start times, latest container start, and
current FactorLab release activation time where known. `stale` is true after
three minutes; missing or invalid snapshots return 503. Historical deployment
times are left null. The endpoint has no Docker socket access.

### Schema map web endpoints

`GET /hub/api/v1/schema-map` reads live `system.tables` and `system.columns`
for the v2 databases (`ref`, `market`, `fundamentals`, `alt`, `book`, `risk`,
`derived`, `broker`, `meta`, `raw`, `research`) and is cached for 60 seconds.
Each table and column carries the plain-language title, summary, notes, and
description from `src/factorlab/api/catalog_curated.json` and
`catalog_descriptions.json`; `areas` describes each database. ClickHouse does
not enforce foreign keys, so relationships are inferred from shared column names
(`V2_FK_TARGETS`) and tagged `identity`, `lineage` (`raw_id`, `ingest_run_id`,
`computation_id`), or `lookup` (country, currency, exchange, and source codes).
`/hub/api/v1/schema-map/v2` is an alias kept for old bookmarks. When the v2
databases are absent, the response is built from the bundled migration DDL and
carries a "Preview mode" warning. The schema explorer lays out every view
automatically; saved layouts and their `PUT …/layout` endpoints were removed with
the legacy map. No table rows are exposed here (see the data catalog endpoints).

### India Markets web endpoints

The `/hub/api/v1/india/...` endpoints power the loopback-only India Markets
page without putting the bearer key in the browser. Instrument rows combine
the complete synchronized reference universe with historical coverage and
checks for a selected trading date. Use the `scope` query parameter to select
`all`, `collecting`, `historical`, or `not_configured` instruments. The response
also includes totals for all four collection views so pagination never hides
the size of the underlying universe. The checks cover expected one-minute
points, missing sessions, OHLC bounds, missing OHLC values, outside-session
bars, and abnormal replacement-version volume.

The per-instrument `days` endpoint uses the XBOM exchange calendar and inserts
missing trading sessions into the response even when ClickHouse contains no
rows for that day. Date ranges are limited to 120 days per request.

### `GET /health`

Checks whether the API process is responding. It does not test every upstream
data source.

```bash
curl -s http://127.0.0.1:8000/health
```

```json
{"status":"ok"}
```

## India one-minute candles

### `GET /api/v1/india/candles/1min`

Returns latest-version Indian candles from `market.bars` and
`market.futures_contract_bars`, ordered from newest to oldest. Equities have a
null `contract_id`; futures have a canonical contract UUID.

### Query parameters

| Parameter | Type | Default | Rules |
|---|---|---:|---|
| `symbol` | string | all | Case-insensitive; 1-40 characters |
| `time_from` | ISO 8601 datetime | none | Inclusive lower bound |
| `time_to` | ISO 8601 datetime | none | Inclusive upper bound; must be after `time_from` |
| `source` | string | all | Currently `upstox` |
| `cursor` | string | none | Opaque `next_cursor` from the prior page |
| `limit` | integer | `500` | 1-1,000 |

All stored and returned timestamps are UTC. For example, NSE open at 09:15 IST
is 03:45 UTC.

### Examples

Latest TCS candles:

```bash
curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/india/candles/1min?symbol=TCS&limit=20"
```

TCS candles in a UTC interval:

```bash
curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/india/candles/1min?symbol=TCS&time_from=2026-08-12T03:45:00Z&time_to=2026-08-12T10:00:00Z&limit=500"
```

Response shape:

```json
{
  "items": [
    {
      "listing_id": "a579e92a-07cf-520a-97e2-64ce18ca5747",
      "contract_id": null,
      "symbol": "TCS",
      "country_code": "IN",
      "bar_time": "2026-08-12T08:09:00",
      "open": "2322.400000",
      "high": "2322.700000",
      "low": "2321.000000",
      "close": "2321.700000",
      "volume": 6375,
      "oi": 0,
      "source": "upstox",
      "as_of_time": "2026-08-12T08:10:09.462000",
      "ingested_at": "2026-08-12T08:10:09.462000"
    }
  ],
  "next_cursor": null,
  "limit": 20,
  "data_as_of": "2026-08-12T08:10:09.462000"
}
```

Decimal price fields are serialized as JSON strings to preserve precision.
`volume` and `oi` may be `null` when the source does not provide them.

## India instruments

### `GET /api/v1/india/instruments`

Returns the synchronized Indian reference universe, ordered by trading symbol.
This is the endpoint for discovering canonical listing IDs and metadata; it is not
limited to instruments that currently have candles.

| Parameter | Type | Default | Rules |
|---|---|---:|---|
| `search` | string | all | Case-insensitive substring of symbol, name, or ISIN |
| `status` | string | all | For example, `active` |
| `source` | string | all | Currently `upstox` |
| `cursor` | string | none | Opaque `next_cursor` from the prior page |
| `limit` | integer | `100` | 1-1,000 |

```bash
curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/india/instruments?search=tata&status=active&limit=100"
```

Each item includes the stable `listing_id`, vendor instrument key, trading
symbol, name, ISIN, exchange and segment metadata, asset type, currency, lot
and tick sizes, status, source, and reference-data timestamps.

## India coverage statistics

### `GET /api/v1/india/stats`

Returns a compact overall summary containing:

- `reference_instruments`: distinct instruments in the synchronized reference universe
- `instruments_with_data`: distinct underlying instrument IDs with stored candles
- `unique_series`: distinct instrument/contract pairs with stored candles
- `data_points`: latest-version one-minute candle rows
- `trading_days`, `first_bar_time`, `last_bar_time`, and `data_as_of`

`date_from`, `date_to`, and `source` optionally filter candle-derived metrics.
The reference-universe count intentionally remains the complete Indian universe.
Dates use Indian trading dates (`Asia/Kolkata`), not UTC calendar dates.

```bash
curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/india/stats?date_from=2026-08-01&source=upstox"
```

### `GET /api/v1/india/stats/daily`

Returns newest-first daily coverage rows. Each row contains `trading_date`,
`data_points`, `unique_instruments`, `unique_series`, first and last UTC bar
timestamps, and `data_as_of`.

| Parameter | Type | Default | Rules |
|---|---|---:|---|
| `date_from` | date | none | Inclusive Indian trading date |
| `date_to` | date | none | Inclusive; must be on or after `date_from` |
| `source` | string | all | Data-source filter |
| `limit` | integer | `90` | 1-1,000 daily rows |

```bash
curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/india/stats/daily?date_from=2026-08-01&limit=30"
```

## India collection observability

These endpoints power collection-health dashboards. `trading_date` always
means the NSE date assigned from `bar_time`; `ingestion_date` is assigned from
`ingested_at`. Both use `Asia/Kolkata`. If omitted, dates default to today in
India.

The poller maintains `india_expected_series` from its configured universe and
records every batch in `ingestion_runs`. This lets the API report missing and
never-seen series instead of considering only data that happened to arrive.

### `GET /api/v1/india/dashboard`

Returns the primary dashboard cards: market state, reference and expected
series counts, actual and expected points, coverage percentage, collection and
backfill counts, latest timestamps, freshness, and anomaly count.

```bash
curl -s -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/india/dashboard?trading_date=2026-08-12"
```

### `GET /api/v1/india/coverage`

Returns expected series with actual points, expected points, coverage,
missing-point count, freshness, and `complete`, `partial`, or `missing` status.
Filters: `trading_date`, `symbol`, `source`, `status`, `min_coverage`, `limit`,
and `offset`.

### `GET /api/v1/india/collection/activity`

Returns a physical-write summary plus hourly and source groupings for an
`ingestion_date`. It separates same-market-date rows from historical backfills.
Physical rows intentionally include stored replacement versions.

### `GET /api/v1/india/freshness`

Returns every active expected series as `live`, `stale`, or `never_seen`.
Parameters: `stale_after_seconds`, `source`, `status`, `limit`, and `offset`.

### `GET /api/v1/india/gaps`

Returns contiguous missing ranges between 09:15 and 15:30 IST. The current
session is evaluated only through its latest expected minute; holidays and
weekends return no expected points. Filters: `trading_date`, `symbol`, `source`,
`limit`, and `offset`.

### `GET /api/v1/india/anomalies`

Detects missing/stale series, OHLC-bound violations, negative values,
out-of-session timestamps, abnormal replacement-version bursts, flatlined closes,
extreme intraday price ranges, and volume spikes. Findings have stable IDs,
severity, observed/expected values, and explanations.

Filters: `trading_date`, `severity`, `symbol`, `source`,
`stale_after_seconds`, `limit`, and `offset`.

### `GET /api/v1/india/metrics/timeseries`

Required parameters are `metric`, `group_by`, `date_from`, and `date_to`.
Ranges are limited to 366 days.

Supported metrics:

- `data_points`, `unique_instruments`, and `unique_series`
- `coverage_percent` and `missing_points`
- `ingestion_lag` and `anomaly_count`

`group_by` is `hour` or `day`; `source` is optional.

### Instrument, run, and source drill-down

- `/api/v1/india/instruments/{listing_id}/summary` combines reference
  metadata, contracts, historical coverage, and today's health.
- `/api/v1/india/ingestion/runs` supports `pipeline`, `source`, `status`,
  `limit`, and `offset`; `/{run_id}` returns one run.
- `/api/v1/india/sources/status` combines the latest run outcome with candle
  freshness. `stale_after_seconds` defaults to 600.

### Historical India coverage snapshot

Live snapshot verified on 2026-08-12:

| Metric | Count |
|---|---:|
| NSE equity instruments in the former `ref_instruments` | 2,464 |
| Symbols currently producing candles | 5 |
| Equity/futures instrument-contract series with candles | 10 |

This August 12 snapshot describes the former legacy collection state. Use the
v2 dashboard and coverage endpoints for current counts after cutover.

## US bars and recovery

The `/api/v1/us` and `/hub/api/v1/us` routes read `ref.listings`,
`market.bars`, `meta.expected_series`, `meta.session_coverage`, and
`meta.recovery_state`. Instrument rows and routes use canonical `listing_id`.
`/candles/daily` and `/candles/1min` return raw OHLCV rows with `listing_id`;
daily rows have no `adj_close`. Cursor tokens contain a v2 marker, query
scope, timestamp, symbol, and listing ID. Restart pagination at cutover;
legacy cursors return `422`.

## Political trades

### `GET /api/v1/political/trades`

Returns latest-version congressional transaction disclosures ordered by
transaction date, newest first.

### Query parameters

| Parameter | Type | Default | Rules |
|---|---|---:|---|
| `ticker` | string | all | Case-insensitive; 1-10 characters |
| `bioguide_id` | string | all | Exactly seven alphanumeric characters |
| `chamber` | enum | all | `house` or `senate` |
| `date_from` | date | none | Inclusive `YYYY-MM-DD` lower bound |
| `date_to` | date | none | Inclusive upper bound; must be after `date_from` |
| `cursor` | string | none | Opaque `next_cursor` from the prior page |
| `limit` | integer | `50` | 1-200 |

```bash
curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/political/trades?ticker=AAPL&chamber=house&limit=20"
```

Each item contains filing identity and dates, legislator identity, asset and
ticker, transaction type/date, reported amount range, source, and ingestion
timestamps. Nullable fields can include `bioguide_id`, `district`, `ticker`,
`notification_date`, `amount_min`, and `amount_max`. The canonical identity is
`political_trade_id`; resolved references use nullable `listing_id`,
`contract_id`, and `legislator_entity_id`. The cursor contains the canonical
political trade ID, and legacy cursors are rejected.

## Political collection observability

Political data has no fixed intraday cadence, so coverage means filing-index
rows successfully parsed into trades plus canonical legislator and ticker
resolution. Filing and transaction dates are plain disclosure dates;
collection activity groups UTC `ingested_at` dates.

### `GET /api/v1/political/dashboard`

Returns current legislator/committee counts, filing and trade totals, parsed
and unparsed filings, unique legislators/tickers, resolution percentages,
late-disclosure and anomaly counts, latest event dates, collection freshness,
and rows collected on `as_of_date`.

### `GET /api/v1/political/coverage`

Returns chamber-level filing parse rates, unparsed backlog, trade totals,
legislator match rates, ticker resolution rates, event coverage, and latest
ingestion time. Optional `year` filters the filing year.

### `GET /api/v1/political/collection/activity`

Returns physical filing/trade rows, unique logical records, and hourly/source
breakdowns for `ingestion_date`. Physical counts include replacement versions.

### `GET /api/v1/political/freshness`

Returns row count, source, latest ingestion, age, and `fresh`, `stale`, or
`never_seen` status for legislators, committees, memberships, filings, and
trades. `stale_after_seconds` defaults to two days.

### `GET /api/v1/political/anomalies`

Detects unparsed filing-index rows, disclosures filed more than 45 days after
the transaction, transaction dates after filing dates, notification dates
before transactions, invalid amount ranges, and unresolved legislators.
Supports date, severity, anomaly-type, limit, and offset filters.

### `GET /api/v1/political/metrics/timeseries`

Supported metrics are `filings`, `trades`, `unique_legislators`,
`unique_tickers`, `amount_min`, `late_disclosures`, and
`unmatched_legislators`. Results group by `day` or `month`, using `filing`,
`transaction`, or `ingestion` date basis. A range cannot exceed ten years.

### Political discovery and drill-down

- `/political/legislators` filters by search, chamber, state, party, and
  current-office status. `/{bioguide_id}/summary` adds disclosure totals.
- `/political/tickers` aggregates unique disclosed tickers. `/{ticker}/summary`
  adds leading legislators and transaction-type mix.
- `/political/ingestion/runs` and `/{run_id}` expose bootstrap outcomes.
- `/political/sources/status` combines latest pipeline outcome and freshness.

## Pagination

The candle, instrument, and political-trade collection endpoints use cursor pagination:

1. Make the first request without `cursor`.
2. Read `next_cursor` from the response.
3. Send it unchanged as the next request's `cursor` query parameter.
4. Stop when `next_cursor` is `null`.

Example using `jq`:

```bash
FIRST=$(curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  "http://127.0.0.1:8000/api/v1/india/candles/1min?symbol=TCS&limit=100")

CURSOR=$(printf '%s' "$FIRST" | jq -r '.next_cursor')

curl -s \
  -H "Authorization: Bearer $FACTORLAB_KEY" \
  --get \
  --data-urlencode "symbol=TCS" \
  --data-urlencode "limit=100" \
  --data-urlencode "cursor=$CURSOR" \
  http://127.0.0.1:8000/api/v1/india/candles/1min
```

Cursors are opaque implementation details. Do not decode, edit, or construct
them in clients.

## HTTP errors

| Status | Meaning |
|---:|---|
| `200` | Successful request |
| `400` | Invalid pagination cursor |
| `401` | Missing or incorrect bearer token |
| `422` | Invalid parameter, range, format, or limit |
| `500` | Unexpected API or database failure |
| `503` | API authentication secret is not configured |

FastAPI validation failures use a JSON `detail` field. Auth failures also send
`WWW-Authenticate: Bearer`.

## Python example

```python
import os
import requests

response = requests.get(
    "http://127.0.0.1:8000/api/v1/india/candles/1min",
    params={"symbol": "TCS", "limit": 100},
    headers={"Authorization": f"Bearer {os.environ['FACTORLAB_API_KEY']}"},
    timeout=30,
)
response.raise_for_status()
page = response.json()
print(len(page["items"]), page["next_cursor"])
```

## Operational checks

```bash
cd /opt/factorlab/deploy

sudo docker compose --env-file production.env \
  -f compose.production.yml ps api ingest-india

sudo docker compose --env-file production.env \
  -f compose.production.yml logs --tail 100 api ingest-india
```

The Upstox token normally expires at 03:30 IST and must be refreshed daily.
The India endpoint can still return already stored ClickHouse data when the
ingestion token is expired, but new candles will stop arriving.
