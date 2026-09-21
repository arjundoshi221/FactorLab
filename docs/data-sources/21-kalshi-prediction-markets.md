# Kalshi — Event Contracts & Prediction Markets

> Status: `[spec]` — no code, no schemas, no ingest yet. This doc is the design target. Sibling spec for Polymarket (crypto-native prediction markets) is deferred until Kalshi lands.

## Purpose

Kalshi is a **CFTC-regulated event-contract exchange** — a legal, US-based prediction market where contracts pay $1 if an event occurs and $0 if it doesn't. Prices between $0 and $1 are the market's implied probability.

For FactorLab, Kalshi is the missing **market-implied probability layer** that pairs with the political-alpha stack:

- `alt_political_us` tells us what politicians **did** (trades, donations, contracts, bills).
- Kalshi tells us what a live-money market **believes** about what will happen next.

The two layers together let us build signals like:

- Bill-passage probability × sectors named in the bill → policy-directional trade.
- Rate-cut probability shift vs. consensus → duration/growth-vs-value rotation.
- Election-outcome probability × congress-trades leaning that way → conviction-weighted political-alpha.
- CPI-print distribution vs. Bloomberg consensus → dispersion trade for banks, TIPS-adjacent equities.
- Geopolitical event probabilities (Taiwan, OPEC) → risk premium in semis, oil, tankers.

Kalshi is chosen over Polymarket as the first venue because:

- **Regulated** (CFTC-registered DCM), so it can legally list US-macro contracts (Fed, CPI, jobs, elections).
- **Deeper US-macro book** — Fed decisions and CPI prints trade with real institutional flow.
- **Clean legal status** for us as a US shop (Polymarket blocks US IPs).
- **Free REST + WebSocket API** with a normal auth flow, no on-chain plumbing.

Polymarket remains interesting for **crypto-native / global** contracts (elections, geopolitics) and for **whale-wallet tracking** — but it slots into a separate `alt_prediction_global` schema later.

## Source

| Host | Purpose |
|------|---------|
| `trading-api.kalshi.com/trade-api/v2` | Production REST — markets, quotes, orderbook, trades, portfolio |
| `api.elections.kalshi.com/trade-api/v2` | Elections-only mirror (some election series live here) |
| `demo-api.kalshi.co/trade-api/v2` | Demo / sandbox for schema validation without KYC |
| `trading-api.kalshi.com/trade-api/ws/v2` | WebSocket — live orderbook, trades, ticker |

The v2 API is the current surface. v1 is deprecated.

## Pricing

**$0 for data.** API access is included with any account at no cost. No paid data tier.

- Account is free. Funding is **not required** for read-only usage.
- Trading fees exist (per-contract, tiered) but do not apply — **FactorLab never places an order on Kalshi.** Same rule as IBKR / Schwab in this repo: read-only, always.

## Auth

Kalshi requires KYC (US residency, SSN, government ID) to create an account — CFTC rules. Once through KYC:

**Two auth modes.** Prefer API-key mode for services; email/password is fine for local scripts.

| Mode | Header | Notes |
|------|--------|-------|
| Email/password → JWT | `Authorization: Bearer {token}` | Token from `POST /login`; short TTL (~30 min); re-login on 401 |
| API key (RSA) | `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP` | Sign `{timestamp}{method}{path}` with the account's RSA private key |

Env vars:

```
KALSHI_API_KEY_ID=<uuid>
KALSHI_API_KEY_PRIVATE_PEM=<path to PEM file, gitignored>
# fallback for local dev only:
KALSHI_EMAIL=<...>
KALSHI_PASSWORD=<...>
```

Private key stays under `data/kalshi/keys/` (gitignored). Never in the repo, never in .env directly.

Public unauthenticated endpoints (`GET /markets`, `GET /events`, `GET /series`) work without any auth and are enough for a v0 read-only ingest — useful for prototyping before KYC completes.

## Rate limits

Kalshi documents tiered per-endpoint limits; conservative worst-case assumptions:

| Endpoint class | Approx limit | Notes |
|---------------|--------------|-------|
| Market data reads (`/markets`, `/events`, `/trades`) | ~10 rps sustained, burst higher | Public + authed both counted |
| Portfolio reads (`/portfolio/*`) | ~10 rps | Authed only |
| Order actions | 30 rps | **Not used** — no trading |
| WebSocket | 1 connection per key, ~200 subs per connection | Use for live tick |

Client design: 100 ms request floor, 5 retries with exponential backoff, honor `Retry-After`. Batch reads by paging `limit=1000` where allowed.

## Endpoints in use

### Reference (public, no auth)

| Path | Returns |
|------|---------|
| `GET /series` | All series (long-lived event templates, e.g. `FED`, `CPIYY`, `KXPRES`) |
| `GET /series/{series_ticker}` | Series metadata + rules |
| `GET /events` | Events (paginated) — each event is one occurrence of a series (e.g. `FED-26DEC`) |
| `GET /events/{event_ticker}` | Event detail + child markets |
| `GET /markets` | All markets (paginated) — each market is one binary contract inside an event |
| `GET /markets/{market_ticker}` | Market detail (yes_bid, yes_ask, volume, open_interest, expiration, rules) |

### Time-series (public)

| Path | Returns |
|------|---------|
| `GET /markets/{ticker}/candlesticks` | OHLC of the yes-side price at 1m / 1h / 1d granularity |
| `GET /markets/trades` | Public trade prints across markets (paginated, filterable by ticker + time) |
| `GET /markets/{ticker}/orderbook` | Current bid/ask ladders (depth ≤50) |

### WebSocket channels (authed)

| Channel | Payload |
|---------|---------|
| `ticker_v2` | Every price change (yes_bid / yes_ask / last) |
| `trade` | Every trade print |
| `orderbook_delta` | Incremental order book updates |
| `market_lifecycle_v2` | New market opens, settlements, expirations |

### Portfolio (authed) — **read-only**

| Path | Purpose |
|------|---------|
| `GET /portfolio/balance` | Account cash — used for sanity, not signal |
| `GET /portfolio/positions` | Our own positions — will always be empty; kept as a **poison-pill check** to detect if trading ever gets accidentally wired in |

## Contract taxonomy — what maps to equity alpha

Kalshi lists thousands of markets. Only a subset is signal for equities. Our ingest **filters at the series level** — we allowlist series that map to a defined equity thesis, and skip the rest (sports, culture, weather-for-fun, etc.).

### Tier A — direct macro (highest priority)

| Series | What it is | Equity signal |
|--------|-----------|---------------|
| `FED` | FOMC rate-decision markets (per meeting: hike/hold/cut in 25 bp bins) | Rate-sensitive equities: banks (KRE, XLF), REITs (VNQ), growth-vs-value (IWF/IWD), long-duration tech |
| `FEDTERMINAL` | Terminal-rate distribution by year-end | Duration positioning, yield-curve trades on ETFs |
| `CPIYY` / `CPICORE` | Monthly CPI print — bins around consensus | TIPS-adjacent equities, breakeven-sensitive names, banks pre-print |
| `NFP` | Non-farm payrolls monthly, bins | Cyclicals vs. defensives ahead of print |
| `UNRATE` | Unemployment rate monthly | Same |
| `GDPQQ` | GDP quarterly, bins | Broad-market beta, cyclicals |
| `RECESSION` | Recession-called-by-{NBER, year-end} | Risk-off regime signal |

### Tier B — politics & policy

| Series | What it is | Equity signal |
|--------|-----------|---------------|
| `KXPRES` (and successors) | Next-presidential-election winner by candidate | Sector rotation: defense, energy, healthcare, EVs, banks (Trump-trade vs. Harris-trade baskets) |
| `KXSENATE`, `KXHOUSE` | Chamber-control probabilities | Tax / regulatory regime probability weighting |
| `KXSTATESENATE-*` | Per-state senate races | Small-cap regional exposures (state banks, utilities) |
| `KXPRIMARY-*` | Primary winners | Same as above, earlier signal |
| `KXBILL-*` (when listed) | Named-bill passage probability | Direct sector trade: crypto bill → COIN, chip act → SOX, drug pricing → biotech |
| `KXNOMINATION-*` | Confirmation of Fed / Treasury / SEC nominees | Financial-sector regime |

### Tier C — geopolitics & commodities

| Series | What it is | Equity signal |
|--------|-----------|---------------|
| `KXUKRAINECEASEFIRE`, `KXISRAEL*`, `KXTAIWAN*` | Ceasefire / escalation / invasion probabilities | Defense (ITA), oil (XLE), tanker rates (STNG, FRO), semis (SOXX, TSM) |
| `KXOPEC-*` | OPEC production-cut decisions | Oil beta, US shale |
| `KXSANCTIONS-*` | Sanctions probabilities on specific countries | Commodity-adjacent equities, EM |
| `KXNATGAS-*` (winter series) | Cold-snap / natgas price markets | UNG, XOP, utility mix |
| `KXHURRICANE-*` | Named-storm landfall | Insurance (TRV, ALL, RE), refiners on Gulf Coast |

### Tier D — corporate / crypto (opportunistic)

| Series | What it is | Equity signal |
|--------|-----------|---------------|
| `KXBTC-*`, `KXETH-*` | Crypto price-threshold markets | COIN, MSTR, MARA, RIOT, HOOD |
| `KXAI-*` | AI-adjacent event markets (model releases, benchmarks) | NVDA-adjacent, MSFT/GOOG basket |
| `KXFDA-*` | FDA approval probabilities (when listed) | Direct biotech alpha |
| `KXMA-*` (M&A closing probability, if listed) | Deal-completion odds | Merger-arb overlay |

### Excluded

Sports, entertainment, weather-for-its-own-sake, celebrity events — no ingest, no storage. Explicitly deny-listed in taxonomy config to keep the DB clean.

## Data model

Four things we care about, in decreasing frequency:

1. **Quote time series** — the yes-side implied probability over time. This is the primary signal input.
2. **Trade prints** — every fill, with size. Trade-size distribution is our conviction proxy (Kalshi doesn't expose per-user wallet-level like Polymarket, so we use trade-size + volume-weighted price as the substitute).
3. **Orderbook depth snapshots** — for liquidity gating (thin markets get discounted in signal weight).
4. **Settlement** — final $0 / $1 outcome, for backtesting signal quality.

Everything is keyed on `market_ticker` (Kalshi-native, e.g. `FED-26DEC-T4.50`), plus our own `event_ticker` and `series_ticker` for rollup.

## Schema — `alt_prediction_us`

New Postgres schema, per-country pattern (Kalshi is US-regulated). Follows the same BCNF / vendor-keyed / raw-payload conventions as `alt_political_us`.

```
alt_prediction_us
├── kalshi_series               (ref)          -- 1 row per series (~100s of rows)
├── kalshi_events               (ref)          -- 1 row per event (~10K rows, low churn)
├── kalshi_markets              (ref)          -- 1 row per market (~100K rows, low churn)
├── kalshi_quotes               (hypertable)   -- yes_bid/ask/mid + volume + OI snapshots
├── kalshi_trades               (hypertable)   -- every trade print
├── kalshi_orderbook_snapshots  (hypertable)   -- optional; top-10 levels each side
├── kalshi_settlements          (fact)         -- final resolution per market
├── kalshi_taxonomy             (config)       -- our tier A/B/C/D classification + equity mapping
└── kalshi_raw                  (archive)      -- raw JSON payloads by fetched_at (audit trail)
```

### Detail — key tables

**`kalshi_series`** — long-lived series definitions (e.g. `FED`, `CPIYY`).

```
series_ticker      text PRIMARY KEY
title              text
category           text            -- Kalshi's own top-level category
frequency          text            -- 'monthly' | 'per_event' | 'continuous'
fetched_at         timestamptz
raw_payload_id     uuid REFERENCES kalshi_raw(id)
```

**`kalshi_events`** — one occurrence of a series.

```
event_ticker       text PRIMARY KEY   -- e.g. 'FED-26DEC'
series_ticker      text REFERENCES kalshi_series
title              text
strike_date        date               -- when the event resolves
mutually_exclusive boolean            -- true if child markets sum to $1
status             text               -- 'active' | 'settled' | 'closed'
fetched_at         timestamptz
raw_payload_id     uuid
```

**`kalshi_markets`** — one binary contract.

```
market_ticker      text PRIMARY KEY   -- e.g. 'FED-26DEC-T4.50'
event_ticker       text REFERENCES kalshi_events
title              text               -- 'Fed cuts to 4.50% at Dec meeting'
yes_sub_title      text
open_time          timestamptz
close_time         timestamptz
expected_expiration_time timestamptz
status             text               -- 'active' | 'settled' | 'closed' | 'determined'
rules_primary      text
rules_secondary    text
fetched_at         timestamptz
raw_payload_id     uuid
```

**`kalshi_quotes`** — TimescaleDB hypertable on `snapshot_at`.

```
snapshot_at        timestamptz NOT NULL
market_ticker      text NOT NULL REFERENCES kalshi_markets
yes_bid            numeric(4,3)      -- $0.000-$1.000
yes_ask            numeric(4,3)
yes_last           numeric(4,3)
volume             bigint            -- cumulative today
volume_24h         bigint
open_interest      bigint
liquidity          numeric           -- Kalshi's own liquidity metric
PRIMARY KEY (market_ticker, snapshot_at)
```

Hypertable interval: 7 days. Retention: keep forever (small — a Fed meeting has ~10 markets × ~10K quotes = 100K rows, trivial).

**`kalshi_trades`** — hypertable on `traded_at`.

```
trade_id           text PRIMARY KEY   -- Kalshi-native
traded_at          timestamptz NOT NULL
market_ticker      text NOT NULL REFERENCES kalshi_markets
side               text              -- 'yes' | 'no' (from taker's perspective)
count              bigint            -- number of contracts
yes_price          numeric(4,3)
no_price           numeric(4,3)
raw_payload_id     uuid
```

**`kalshi_orderbook_snapshots`** — hypertable on `snapshot_at`, optional (only for tier-A markets).

```
snapshot_at        timestamptz NOT NULL
market_ticker      text NOT NULL
side               text              -- 'yes' | 'no'
level              smallint          -- 1..10
price              numeric(4,3)
size               bigint
PRIMARY KEY (market_ticker, snapshot_at, side, level)
```

**`kalshi_settlements`**

```
market_ticker      text PRIMARY KEY REFERENCES kalshi_markets
settled_at         timestamptz NOT NULL
result             text NOT NULL     -- 'yes' | 'no' | 'void'
raw_payload_id     uuid
```

**`kalshi_taxonomy`** — our editorial layer, hand-curated, versioned in code and re-hydrated on migration.

```
series_ticker           text PRIMARY KEY REFERENCES kalshi_series
tier                    text NOT NULL     -- 'A' | 'B' | 'C' | 'D' | 'excluded'
thesis                  text              -- one-line why-we-care
mapped_universe         text[]            -- e.g. {'XLE','XOP','FANG'}
mapped_sector           text              -- GICS sector name for rollups
mapped_factor           text              -- 'rate_sensitive' | 'geopolitical_risk' | 'policy_directional' | ...
notes                   text
```

The taxonomy is the **only** place editorial judgment lives — every downstream signal reads from here, so we can re-classify without rebuilding facts.

**`kalshi_raw`** — same shape as other `*_raw` archives in the repo. Full payload + endpoint + fetched_at, uuid-keyed, no dedup at write time.

## Ingest cadence

Three loops, tiered by market activity:

| Loop | Cadence | What runs | Scripts |
|------|---------|-----------|---------|
| `kalshi_reference_daily` | Daily 04:00 ET | Refresh `kalshi_series`, `kalshi_events`, `kalshi_markets`; pick up new listings | `scripts/us/prediction/us_prediction_kalshi_reference.py` |
| `kalshi_quotes_polling` | 1 min (tier A), 5 min (tier B), 15 min (tier C/D) | `GET /markets/{ticker}` for allowlisted tickers → `kalshi_quotes` | `scripts/us/prediction/us_prediction_kalshi_quotes.py` |
| `kalshi_stream_live` | Continuous (daemon) | WebSocket `ticker_v2` + `trade` → `kalshi_quotes` + `kalshi_trades` in near-real-time | `scripts/us/prediction/us_prediction_kalshi_stream.py` |
| `kalshi_settlements_daily` | Daily 06:00 ET | Sweep any market where `strike_date < today` and settlement missing | `scripts/us/prediction/us_prediction_kalshi_settlements.py` |

Naming follows `dev-008` convention (`{country}_{domain}_{vendor}_{action}`).

Polling loop is the **primary** source (survives if WS drops); stream is a low-latency overlay for tier-A markets around print/decision windows.

## Storage strategy

- Postgres + TimescaleDB is canonical (same as prices / political trades).
- Hypertables: `kalshi_quotes`, `kalshi_trades`, `kalshi_orderbook_snapshots`.
- Parquet mirror under `data/kalshi/` for analytical work (DuckDB / polars); one file per (series, event) pair, refreshed daily.
- Raw JSON payloads written to filesystem under `data/kalshi/raw/{yyyy}/{mm}/{dd}/` and pointered from `kalshi_raw` — same pattern as political raw (keeps DB slim, bytes on disk).

Size budget (rough):

- Reference: ~10 MB total
- Quotes: ~100 markets active × 1440 min/day × 365 days × ~40 bytes = ~2 GB/yr in Postgres, negligible with compression
- Trades: depends on venue, expect ~10 GB/yr worst case
- Raw: ~20 GB/yr on disk

Comfortably fits alongside political.

## Signal thesis — what we actually do with it

Once ingested, four analytics products live in `derived`:

**`derived.kalshi_implied_prob`** — clean, forward-filled implied-probability time series per market, dividend of `yes_bid` and `yes_ask` midpoint with liquidity gating.

**`derived.kalshi_prob_shift`** — day-over-day and hour-over-hour probability changes. The **shift** is often the alpha, not the level.

**`derived.kalshi_consensus_gap`** — for macro-print series (CPI, NFP, Fed), compare Kalshi implied distribution vs. Bloomberg / Reuters consensus and vs. Fed dot-plot. Gap = disagreement signal.

**`derived.kalshi_sector_tilt`** — for policy / election / geopolitical series, map probability shifts through `kalshi_taxonomy.mapped_universe` into a sector-tilt vector. Weight tilts by liquidity and by prob-shift magnitude.

Downstream, these feed:

- **Political-alpha overlay** — cross `derived.kalshi_sector_tilt` with `alt_political_us.legislator_trades_dedup` for conviction-weighted political signal.
- **Pre-print positioning screen** — flag equities with large deviation between Kalshi-implied and consensus print (banks, TIPS-adjacent).
- **Regime detector** — recession / Fed-pivot probability as a top-of-book regime variable for factor rotation.

## Compliance / ToS

- Kalshi ToS permits programmatic data access for personal / internal research. Explicit ban on **rehosting** raw data as a competing product — fine for us (never republished).
- Written record kept: `KALSHI_API_KEY_ID` is tied to an individual account. Rate limits and read-only rules enforced client-side (same guardrails as Schwab / IBKR).
- Trading endpoints are **structurally excluded** from the client — the trade methods do not exist in `src/factorlab/sources/kalshi/`. Same pattern as Schwab (`docs/data-sources/07-us-equities-schwab.md` calls out "Schwab is read-only"). This is not a config flag we can flip — the code isn't there to flip.

## Roadmap

**Phase 1 — read-only ingest (target: 2 weeks after schema lands)**
- `src/factorlab/sources/kalshi/` client (REST + WS).
- Schema migration for `alt_prediction_us`.
- Reference + quotes-polling loops.
- Backfill last 12 months of tier-A markets via candlesticks endpoint.

**Phase 2 — stream + full history**
- WebSocket daemon for tier-A live tick.
- Trades ingest.
- Settlements sweep.

**Phase 3 — derived signals**
- `derived.kalshi_implied_prob`, `prob_shift`, `consensus_gap`, `sector_tilt`.
- Cross-signals with `alt_political_us`.

**Phase 4 — Polymarket (separate spec)**
- New schema `alt_prediction_global`.
- On-chain (Polygon) ingestion for whale-wallet tracking.
- Focus: elections + geopolitics where Polymarket has depth Kalshi lacks.

## Open questions

- **Wallet-equivalent for conviction?** Kalshi doesn't expose per-account positions publicly. Best substitute is trade-size distribution + open-interest concentration — needs validation against known events to see if it actually flags conviction moves.
- **Backfill depth on candlesticks?** Kalshi retains historical candles but the depth per series varies. Need to probe endpoint before committing a "12 months" backfill target.
- **Consensus source for macro-gap signals?** Bloomberg is expensive; Reuters / Refinitiv same. Investing.com's consensus is free but license-fuzzy. TBD — could start with Fed dot-plot (public) for Fed series only.
- **Do we listen to `market_lifecycle_v2` in near-real-time to auto-add new markets to allowlist,** or accept the daily reference refresh lag? For most series the daily loop is fine; consider live only if we ever want to trade around new-listing edges (we don't, since read-only).
- **Storage for orderbook depth** — worth it for tier-A only, or skip entirely? Depth matters most for liquidity gating; a `min_liquidity` field on `kalshi_quotes` may be enough without full ladders.
