# US Equities — Charles Schwab

> Status: `[active]` — auth + intraday + daily fetchers, auto-seed, 3-tier polling all wired (2026-05-01)
>
> Schema target: `market_us.fact_equity` (per-bar `freq_id` + `session` + `endpoint_id` FKs).

## Purpose
Free 20+ year US price history, basic fundamentals (ratios), real-time quotes, and options chains via a funded brokerage account. Supplements EODHD (deep fundamentals) and IBKR (clean exchange-feed data).

## Source
**Charles Schwab Trader API** — REST + WebSocket, JSON responses, all US equities/options.

## Account Requirements
- Standard Schwab brokerage account ($0 minimum, deposit $1 to activate)
- Sign NYSE, NASDAQ, OPRA exchange agreements on schwab.com for real-time data
- Register at `developer.schwab.com` with Schwab login
- Create an app → wait for approval (1-3 days, up to 2-3 weeks)
- API access is free — no separate fee

## Account activity & dormancy
- **No inactivity fees.** Schwab's standard brokerage account has no annual maintenance fee and no inactivity fee ([Schwab Pricing](https://www.schwab.com/pricing), [FAQ](https://www.schwab.com/node/46541)). A funded, non-trading account is not auto-closed for fee reasons.
- **Escheatment is the real risk on prolonged idleness.** State unclaimed-property laws — not Schwab policy — force transfer of assets to the state after a dormancy period of typically **3–5 years** with no customer-initiated contact. Many states have shortened the trigger from 7 → 3 years and now require *affirmative customer contact* (not just returned-mail absence). The account is recoverable in perpetuity from the state, but the brokerage balance is emptied.
- **What counts as contact.** Logging into `client.schwab.com`, opening a statement, placing a trade, or moving cash. Activity on *any* account under the same Schwab client ID (e.g. Schwab Bank checking) preserves dormancy status for the brokerage account too, since they share the client relationship ([Schwab Bank Regulatory Booklet](https://www.schwab.com/resource/schwab-bank-regulatory-booklet)).
- **API token refresh alone is not a substitute.** The weekly OAuth re-login hits `schwab.com` and is *technically* a login event, but escheatment statutes often require a customer-initiated transaction or written contact. Don't rely on the cron alone.
- **Developer-portal app inactivity** has no publicly documented expiration. The only documented timer is the 7-day refresh token. The weekly OAuth flow keeps the app exercised regardless.

## Auth

### Overview
- **OAuth 2.0 Authorization Code Grant** (same pattern as Upstox)
- Access token: **30 minutes** (auto-refreshed by `schwab-py`)
- Refresh token: **7 days** (must re-login via browser + MFA weekly)
- Redirect URI: `https://127.0.0.1:8182` (HTTPS required, even localhost)
- Could deploy Railway callback server (same pattern as Upstox auth server)

### Token endpoints
```
Authorize: https://api.schwabapi.com/v1/oauth/authorize
Token:     https://api.schwabapi.com/v1/oauth/token
```

### .env
```
SCHWAB_APP_KEY=...
SCHWAB_APP_SECRET=...
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
SCHWAB_TOKEN_PATH=data/schwab/.token
```

### Setup steps (one-time)
1. Open Schwab brokerage at schwab.com (deposit $1)
2. Sign NYSE/NASDAQ/OPRA exchange agreements on schwab.com
3. Register at `https://beta-developer.schwab.com/` with Schwab login
4. Create app:
   - API Product: **"Market Data Production"** (covers `/marketdata/v1/...` — quotes, pricehistory, chains, instruments, movers)
   - "Accounts and Trading Production" may also be enabled on the app, but **FactorLab MUST NOT call any `/trader/v1/...` endpoint**. See "Hard rule: no trading" below.
   - Order Limit: **120 requests/minute**
   - Callback URL: **`https://127.0.0.1:8182`** (must match exactly — case, port, no trailing slash)
5. Wait for approval: status goes from "Approved - Pending" → "Ready For Use" (1-3 days, up to 2-3 weeks)
6. Save App Key + App Secret (secret shown only once)
7. **OAuth login uses your regular Schwab brokerage credentials** (the same ones you use at schwab.com), NOT a separate developer account. App Key/Secret are embedded in the request — never typed at the login screen.

### Hard rule: no trading
- FactorLab is **research-only**. The Schwab integration is read-only market data.
- Even if the app has "Accounts and Trading Production" enabled at the portal level, the codebase MUST NOT:
  - Place, replace, or cancel orders (`POST /trader/v1/accounts/{id}/orders`, etc.)
  - Read positions, balances, or transactions (`GET /trader/v1/accounts/...`)
  - Import any `schwab.client` order/account methods (`place_order`, `cancel_order`, `get_account*`, `get_orders*`, `get_transactions*`)
- The Schwab source package (`src/factorlab/countries/us/equities/schwab/`) MUST expose only market-data wrappers (quotes, price history, chains, instruments, movers, market hours).
- Reviewers: reject any PR that introduces a trading or account endpoint call here.

### Python auth (schwab-py)

**Recommended: `easy_client()` — handles all flows automatically:**
```python
from schwab.auth import easy_client

client = easy_client(
    api_key=os.getenv('SCHWAB_APP_KEY'),
    app_secret=os.getenv('SCHWAB_APP_SECRET'),
    callback_url=os.getenv('SCHWAB_CALLBACK_URL'),
    token_path=os.getenv('SCHWAB_TOKEN_PATH'),
)
# First run: opens browser for OAuth login + MFA
# Subsequent runs: loads token file, auto-refreshes within 7-day window
```

**Explicit first-time flow:**
```python
from schwab import auth

client = auth.client_from_login_flow(
    api_key=os.getenv('SCHWAB_APP_KEY'),
    app_secret=os.getenv('SCHWAB_APP_SECRET'),
    callback_url=os.getenv('SCHWAB_CALLBACK_URL'),
    token_path=os.getenv('SCHWAB_TOKEN_PATH'),
)
```

**Explicit token-file flow (for cron/scripts):**
```python
client = auth.client_from_token_file(
    token_path=os.getenv('SCHWAB_TOKEN_PATH'),
    api_key=os.getenv('SCHWAB_APP_KEY'),
    app_secret=os.getenv('SCHWAB_APP_SECRET'),
)
```

### Auth gotchas
- **OAuth login screen wants brokerage credentials**, not a dev-portal-specific account. If your `schwab.com` brokerage login doesn't work at the OAuth screen, log into `schwab.com` first to confirm the credentials work and finish any device/MFA flow.
- **"401 Unauthorized" / "assertion_rejected"** — app is still in "Approved - Pending" state. Wait for "Ready for Use".
- **Browser SSL warnings** — `client_from_login_flow()` uses self-signed cert for local HTTPS. Click Advanced → Proceed.
- **Windows Firewall** — first OAuth attempt may prompt to allow `python.exe` to listen on `127.0.0.1:8182`. Allow once.
- **Windows console encoding** — scripts that `print` Unicode (e.g. `→`) crash on cp1252. Use ASCII output in driver scripts.
- **Never manually edit token files** — `schwab-py` manages the entire lifecycle. Token format is `{creation_timestamp, token: {access_token, refresh_token, expires_at, scope, ...}}`. Modifying it causes parsing failures.
- **Token auto-refresh is lazy** — `schwab-py`/authlib refreshes the 30-min access token before the next request when `expires_at - now < 300s`. Not a background thread.
- **Cloud/headless** — no browser? Use `client_from_manual_flow()` (paste-URL), or create token on desktop and transfer `.token` JSON to server. Or deploy Railway callback (same as Upstox).
- **Weekend re-auth** — refresh token expires after exactly 7 days. `easy_client(max_token_age=6.5d)` proactively forces re-OAuth at 6.5 days. Re-authenticate on weekends to avoid Monday morning failures.

## Rate limits

### Documented limits
| Limit | Value | Notes |
|-------|-------|-------|
| Requests per minute | **120** | App-level, set during app creation |
| Streaming connections | **1** per account | WebSocket — reconnect logic required |
| Throttle response | HTTP 429 | Check `Retry-After` header |
| Sustained safe rate | ~2 req/sec | Community consensus |

### Practical throughput
- 500-stock daily quote refresh: ~500 requests = ~4 minutes at 2/sec
- 500-stock 20yr daily backfill: ~500 requests = ~4 minutes (1 req per stock, returns full history)
- `schwab-py` does NOT auto-retry on 429 — implement backoff yourself

### Price history data retention
| Frequency | Max lookback |
|-----------|-------------|
| 1-minute | ~48 days |
| 5/10/15/30-minute | ~9 months |
| Daily | **20+ years** (some back to 1985) |
| Weekly/monthly | Full history |

## Endpoints

| Endpoint | Use | Notes |
|----------|-----|-------|
| `GET /marketdata/v1/quotes?symbols=AAPL,MSFT` | Real-time/delayed quotes **+ inline fundamentals + reference (CUSIP)** | Batch in one call; verified 50 symbols in 1.5s |
| `GET /marketdata/v1/pricehistory?symbol=AAPL` | Historical OHLCV bars | Daily 20yr+ (5,367 bars 2005→2026 verified for AAPL); minute ~30-60 days |
| `GET /marketdata/v1/chains?symbol=AAPL` | Options chains + Greeks | Full US options |
| `GET /marketdata/v1/instruments?symbol=AAPL&projection=fundamental` | **Full fundamental ratios + margins + ROE/ROA + solvency + beta + short interest** | 56 fields per symbol, 1 API call per request — see "Fundamentals projection" below |
| `GET /marketdata/v1/instruments?symbol=Apple&projection=symbol-search` | Instrument search | Symbol/name lookup |
| `GET /marketdata/v1/movers/$SPX.X` | Market movers | S&P 500, Dow, NASDAQ |
| `GET /marketdata/v1/markets?markets=equity` | Market hours | Trading calendar |

### Fundamentals projection (verified 2026-05-01, AAPL)

Calling `get_instruments(symbols, projection=FUNDAMENTAL)` returns 56 fields per symbol — way more than the 16 keys inline on the quote response. **One API call per request**, not 10 like EODHD.

**Categories (56 fields total):**
- **Margins (6)**: `grossMarginMRQ`, `grossMarginTTM`, `netProfitMarginMRQ`, `netProfitMarginTTM`, `operatingMarginMRQ`, `operatingMarginTTM`
- **Returns (3)**: `returnOnAssets`, `returnOnEquity`, `returnOnInvestment`
- **Per-share (3)**: `bookValuePerShare`, `eps`, `epsTTM`
- **Growth (6)**: `epsChange`, `epsChangePercentTTM`, `epsChangeYear`, `revChangeIn`, `revChangeTTM`, `revChangeYear`, `divGrowthRate3Year`
- **Solvency (6)**: `currentRatio`, `quickRatio`, `ltDebtToEquity`, `totalDebtToEquity`, `totalDebtToCapital`, `interestCoverage`
- **Risk (1)**: `beta`
- **Valuation ratios (5)**: `peRatio`, `pegRatio`, `pbRatio`, `pcfRatio`, `prRatio`
- **Short interest (2)**: `shortIntToFloat`, `shortIntDayToCover`
- **52-week (2)**: `high52`, `low52`
- **Dividends (9)**: `dividendAmount`, `dividendYield`, `dividendFreq`, `dividendDate`, `declarationDate`, `dividendPayAmount`, `dividendPayDate`, `nextDividendDate`, `nextDivPayDate`
- **Volume averages (4)**: `avg1DayVolume`, `avg10DaysVolume`, `avg3MonthVolume`, `dtnVolume`
- **Market cap (3)**: `marketCap`, `marketCapFloat`, `sharesOutstanding`
- **Misc (6)**: `vol1DayAvg`, `vol10DayAvg`, `vol3MonthAvg`, `fundLeverageFactor`, `symbol`

**What this projection does NOT give** (use EODHD for these):
- Full financial statements (income statement, balance sheet, cash flow)
- Earnings history time series (130 quarters of EPS)
- Splits & dividends history
- Analyst ratings + price targets
- Top institutional holders / funds breakdown
- Insider transactions

### Option chain (verified 2026-05-01, AAPL)

`get_option_chain(symbol, ...)` returns **52 fields per contract** including full Greeks and IV — enterprise-grade options data, free with brokerage account.

**Top-level payload** (~1 MB for 10 strikes near ATM, all expirations):
```
symbol, status, underlying (full quote), strategy, interval, isDelayed, isIndex,
interestRate, underlyingPrice, volatility, dividendYield, daysToExpiration,
numberOfContracts, callExpDateMap, putExpDateMap
```

**Per-contract fields:**
- **Greeks**: `delta`, `gamma`, `theta`, `vega`, `rho`
- **IV**: `volatility` (per-contract IV), `theoreticalVolatility` (model IV)
- **Pricing**: `bid`, `ask`, `last`, `mark`, `theoreticalOptionValue`, `timeValue`, `intrinsicValue`, `extrinsicValue`
- **Volume**: `totalVolume`, `openInterest`
- **Expiration**: `expirationDate`, `daysToExpiration`, `lastTradingDay`, `expirationType`
- **Strike**: `strikePrice`, `inTheMoney`
- **Contract specs**: `multiplier`, `settlementType`, `exerciseType`, `optionDeliverablesList`
- **Flags**: `pennyPilot`, `mini`, `nonStandard`

`interestRate` and `dividendYield` are top-level so Black-Scholes inputs are inline. For full chain (all strikes), omit `strike_count` (expect MB-size payloads).

### Movers (verified 2026-05-01, SPX)

`get_movers(index, sort_order)` — 10 movers per call.
- Index enum: `SPX`, `COMPX`, `DJI`, `NASDAQ`, `NYSE`, `EQUITY_ALL`, `INDEX_ALL`, `OPTION_ALL`
- Sort: `PERCENT_CHANGE_UP`, `PERCENT_CHANGE_DOWN`, `VOLUME`, `TRADES`
- **Caveat**: `volume` field is index-aggregate (same across rows). Use `totalVolume` per-symbol.
- Per-mover fields: `symbol, description, lastPrice, netChange, netPercentChange, totalVolume, marketShare, trades, volume`
- Limited utility for systematic — primarily a UI/screening helper.

### Market hours (verified 2026-05-01, equity)

`get_market_hours(markets=[EQUITY])` — ~700 bytes per call.
- Returns `equity.EQ.{date, isOpen, sessionHours: {preMarket, regularMarket, postMarket}}`
- Times in ET ISO format with timezone offset (e.g. `2026-05-01T09:30:00-04:00`)
- `preMarket`: 07:00–09:30 ET, `regularMarket`: 09:30–16:00 ET, `postMarket`: 16:00–20:00 ET
- Cleaner than `exchange_calendars` for cron timing decisions; defer to library for historical sessions.

### Price history parameters
```
periodType:    day | month | year | ytd
frequencyType: minute | daily | weekly | monthly
```

| periodType | periods | frequencyTypes |
|-----------|---------|----------------|
| day | 1-10 | minute |
| month | 1,2,3,6 | daily, weekly |
| year | 1,2,3,5,10,15,20 | daily, weekly, monthly |
| ytd | 1 | daily, weekly |

Symbol format: plain tickers — `AAPL`, `MSFT`. **Class shares use `/` not `.`** — `BRK/B`, `BRK/A`, `BF/A`, `BF/B`. Sending `BRK.B` returns it in `errors.invalidSymbols`. Normalize at the boundary (`.` ↔ `/`).

## Schema mapping (vendor → canonical)

### Price history response (verified 2026-05-01)
```json
{
  "symbol": "AAPL",
  "empty": false,
  "candles": [
    {"open": 1.15875, "high": 1.16071, "low": 1.14339, "close": 1.15,
     "volume": 278588800, "datetime": 1104472800000}
  ]
}
```
Always check `empty: false` before iterating `candles`.

Maps to `market.price_bars_daily`:

| Schwab field | Canonical column | Notes |
|-------------|------------------|-------|
| `datetime` (epoch ms, midnight ET in UTC) | `trade_date` | truncate to date |
| `open`/`high`/`low`/`close` | `open`/`high`/`low`/`close` | already split-adjusted |
| (synthesized: copy `close`) | `adj_close` | Schwab pre-adjusts; raw close not available |
| `volume` | `volume` | also split-adjusted |
| (request time) | `as_of_time` | |
| `'schwab'` | `source` | |

### Quote response (verified 2026-05-01)
Per-symbol payload has 11 top-level keys. Useful blocks:

- **`reference`**: `cusip` (primary cross-source key), `description`, `exchange` (`Q`=NASDAQ, `N`=NYSE), `isShortable`, `htbRate`, `optionable`
- **`quote`** (28 keys): `lastPrice`, `closePrice` (prev close), `openPrice`, `highPrice`, `lowPrice`, `totalVolume`, `bidPrice`/`askPrice` + sizes, `52WeekHigh`/`52WeekLow`, `quoteTime`/`tradeTime` (epoch ms), `mark`, `quoteType` (`NBBO`), `realtime` (bool — `true` confirms exchange agreements)
- **`fundamental`** (16 keys, free): `peRatio`, `eps`, `divYield`, `divAmount`, `divFreq`, `divExDate`, `divPayDate`, `nextDivExDate`, `nextDivPayDate`, `declarationDate`, `lastEarningsDate`, `sharesOutstanding`, `avg10DaysVolume`, `avg1YearVolume`
- **`regular`** (5): regular-hours snapshot
- **`extended`** (10): extended-hours bid/ask

Batch response top-level: `{"AAPL": {...}, "MSFT": {...}, ..., "errors": {"invalidSymbols": [...]}}`. Always inspect `errors.invalidSymbols`.

### Identifiers
- Canonical ticker stored in `ref.instruments.trading_symbol` (e.g. `BRK.B`).
- Translation to Schwab's API form (`BRK/B`) happens in code at the API boundary via [`to_schwab()`](../../../src/factorlab/countries/us/equities/schwab/symbols.py) — no DB-side mapping today.
- Cross-source key (`cusip`) is available on `quote.reference.cusip` and intended to land on `ref.instruments` eventually; for now we rely on canonical ticker as the join key.
- A future `ref.security_aliases` table (referenced in `configs/sources/schwab.yaml`) will materialize per-vendor mappings when IBKR is wired up — IBKR's opaque `conId` has no algorithmic translation. Until then, the algorithmic helper is sufficient.

### Tracing
Log on every request for support tickets:
- `schwab-client-correlid` — UUID, primary identifier
- `x-request-id` — UUID

No `X-RateLimit-*` headers under budget; only `Retry-After` on 429.

## What Schwab provides that others don't
- **20+ year daily history** for free, split-adjusted (EODHD needs paid plan for >1yr)
- **Real-time quotes** for free with exchange agreements signed (IBKR needs $10/mo subscription)
- **Inline fundamentals on every quote response** — `peRatio`, `eps`, `divYield`, `divAmount`, full dividend schedule, `sharesOutstanding`, `avg10DaysVolume`, `avg1YearVolume`. No extra API call needed.
- **CUSIP on every quote** via `reference.cusip` — primary cross-source identifier
- **Options chains** with Greeks for free
- **NBBO real-time top-of-book** (bid/ask + sizes + MICs) included in quote

## What Schwab does NOT provide
- **Full financial statements** (income, balance sheet, cash flow) — use EODHD Fundamentals
- **Earnings history time series** (130 quarters of EPS, annual, trend) — use EODHD
- **Corporate actions API** (splits, dividends history) — use EODHD
- **Analyst ratings + price targets** — use EODHD
- **Institutional holders / funds breakdown** — use EODHD
- **Insider transactions** — use EODHD
- Intraday history beyond ~30-60 days — use IBKR
- Non-US market data — use EODHD/IBKR
- Bulk download (entire exchange) — use EODHD
- Level 2 / depth of book — use IBKR

## Edge cases & gotchas
- **Weekly re-auth required** — 7-day refresh token. Must complete browser OAuth + MFA every week.
- **No headless auth** — browser interaction mandatory. Railway callback pattern recommended.
- **Schwab returns adjusted prices AND adjusted volume** — splits applied to both `close` and `volume`. Verified for AAPL across the 2020-08-31 4:1 split. Compare carefully with EODHD raw prices.
- **No raw close field** — Schwab pre-adjusts. For raw prices, use EODHD or IBKR.
- **Class-share symbols use `/` not `.`** — `BRK/B`, `BF/A`. Wrong format → silent fail (added to `errors.invalidSymbols`).
- **Datetime convention** — price history `datetime` is epoch ms representing midnight ET expressed in UTC. Always truncate to date for daily bars.
- **Batch quote silent failures** — invalid symbols don't return a per-symbol error; they get aggregated under top-level `errors.invalidSymbols`. Always inspect.
- **App approval delays** — 1-3 days typical, can take 2-3 weeks. Apply early.
- **Encrypted account IDs** — API returns hashed account numbers, not your actual number.
- **Intraday data is shallow** — only ~30-60 days of minute bars. Not suitable for historical intraday research.
- **Post-TDA migration** — the API is the successor to TD Ameritrade's API. Some endpoints behave differently from TDA docs.
- **Exchange agreements** — must sign NYSE/NASDAQ/OPRA agreements on schwab.com before real-time data flows. `quote.realtime: true` confirms they're working.
- **Windows console** — `print()` of Unicode chars (`→`, etc.) crashes on cp1252. Keep driver scripts ASCII-only.

## Library
```bash
pip install schwab-py
```
- GitHub: https://github.com/alexgolec/schwab-py
- Docs: https://schwab-py.readthedocs.io/
- Author: Alex Golec (same as the beloved `tda-api`)

## Open questions
- [ ] Deploy Railway callback server for Schwab OAuth (same pattern as Upstox)? — defer until weekly browser OAuth feels painful.
- [x] ~~Token refresh cadence: cron job every 25 minutes, or on-demand?~~ — `schwab-py` auto-refreshes lazily before each request via authlib (5-min leeway). No cron needed.
- [x] ~~Use Schwab as primary for US daily backfill?~~ — Yes. Schwab gives 20yr split-adjusted daily for free + inline fundamentals. Demote EODHD to: (a) raw prices for splits/corp-action analysis, (b) full financial statements ($60/mo upgrade) only when we need income/balance/cashflow.
- [x] ~~Streaming: worth setting up WebSocket for real-time quotes?~~ — **Deferred**. REST + 3-tier polling covers research needs (R3K daily + S&P 500 live 5m + watchlist live 1m). Streaming becomes worth the complexity only when (a) live trading on Russell-3000-wide signals, (b) sub-minute latency matters. Design notes preserved in `playground/explore/schwab/NOTES.md`.

## Implemented modules + scripts (2026-05-01)

### Source package
| Path | Purpose |
|------|---------|
| `src/factorlab/countries/us/equities/schwab/auth.py` | `ensure_client(interactive)` — token-file load → schwab-py auto-refresh; mirrors Upstox `ensure_token` pattern. Token at `data/schwab/.token`. |
| `src/factorlab/countries/us/equities/schwab/client.py` | `get_client(interactive=False)` thin wrapper |
| `src/factorlab/countries/us/equities/schwab/symbols.py` | `to_schwab() / from_schwab()` — `BRK.B` ↔ `BRK/B` boundary translator |
| `src/factorlab/countries/us/equities/schwab/candles.py` | `fetch_daily_bars(client, symbol, from_date, to_date)` → DataFrame; `bar_time` UTC midnight, `adj_close = close` (Schwab pre-adjusts) |
| `src/factorlab/countries/us/equities/schwab/intraday.py` | `fetch_intraday(client, symbol, frequency, start, end)` — 1m/5m/10m/15m/30m. Dedups Schwab's 2× quirk; tags `session` from ET timestamp |
| `src/factorlab/countries/us/equities/schwab/quotes.py` | `fetch_quotes(client, symbols)` → `QuoteBundle`; `fetch_fundamentals_projection(client, symbols)` → 56-field DataFrame |

### Driver scripts
| Script | Purpose | Cadence |
|--------|---------|---------|
| `scripts/us/equities/schwab/us_equities_schwab_auth.py` | OAuth bootstrap + weekly re-auth (browser + MFA) | Weekly |
| `scripts/us/equities/blackrock/us_equities_blackrock_universe.py` | Build US universe yamls from public iShares IWV ETF holdings | Quarterly |
| `scripts/us/equities/schwab/us_equities_schwab_historical.py` | One-shot CLI backfill — any universe, any frequency, `--from-date`/`--to-date`. Auto-seeds `ref.instruments`. | Manual / on-demand |
| `scripts/us/equities/schwab/us_equities_schwab_live.py` | 1m intraday catch-up cron. Pulls last 2h of 1m bars for SP500 every hour. Idempotent upsert. 24/7 schedule (off-hours are cheap no-ops). | Hourly cron |
| `scripts/us/equities/schwab/us_equities_schwab_eod.py` | Daily catch-up cron. Pulls last 7d of daily bars for R3K. Idempotent upsert. | Daily post-close cron |
| `src/factorlab/countries/us/equities/schwab/_jobs.py` | Shared helpers used by all three above (universe loader, instrument auto-seeder, fetch dispatcher with 429/timeout retry) | n/a (library) |

### Universes
| Yaml | Symbols | Built by |
|------|---------|----------|
| `configs/universes/us_watchlist.yaml` | 25 | hand-curated mega-caps |
| `configs/universes/us_sp100.yaml` | 100 | universe-builder `--top 100` |
| `configs/universes/us_sp500.yaml` | 500 | universe-builder `--top 500` |
| `configs/universes/us_russell3000.yaml` | ~2,578 | universe-builder full IWV |

## Recommended 3-tier polling strategy

Schwab's 120 req/min budget caps live REST polling. Russell 3000 (2,578 symbols) overflows any cadence by 4×. Slice the universe by latency requirement:

| Tier | Universe | Cadence | Calls/min | Latency to DB |
|------|----------|---------|-----------|---------------|
| 1 — End-of-day batch | Russell 3000 (~2,578) | Daily cron at 16:30 ET | ~100 over ~25 min run | ~30 min after close |
| 2 — Live broad | S&P 500 (500) | Every 5 min, daemon | 100 sustained | ~5 min |
| 3 — Live watchlist | 25 mega-caps | Every 1 min, daemon | 25 sustained | ~1 min |

For Russell-3000-wide sub-minute data, use streaming (deferred). REST cannot.

### Backfill timing (verified 2026-05-01, post-executemany optimization)

| Frequency | Per-symbol time | R3K total (4 workers) |
|-----------|-----------------|------------------------|
| 1d, 20yr | ~2s | ~22 min |
| 5m, 8.5mo | ~5s | ~55 min |
| 1m, 45 days | ~11s (38K bars/symbol) | ~1.5 hours |

API call alone is ~1.5s; the rest is row insert via SQLAlchemy executemany. Pre-optimization (row-by-row) was 6× slower.

## Operational runbook

### Weekly (Sunday, before 7-day token expiry)
```bash
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/schwab/us_equities_schwab_auth.py
```
Browser opens → Schwab login + MFA → token saved to `data/schwab/.token`.

### Daily after market close (Mon-Fri ~17:30 ET / 03:00 IST next day)
Runs automatically via the `Schwab-EOD` cron (registered in Task Scheduler):
```bash
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/schwab/us_equities_schwab_eod.py
```
This pulls the last 7 days of daily R3K bars (overlap absorbs any missed days). Idempotent — re-runs are safe. For a deeper historical refill, use the historical script:
```bash
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/schwab/us_equities_schwab_historical.py \
    --universe russell3000 --frequency 1d --from-date 2020-01-01 --workers 4
```

### Quarterly (first Sunday after each quarter-end)
```bash
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/blackrock/us_equities_blackrock_universe.py --target russell3000
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/blackrock/us_equities_blackrock_universe.py --target sp500 --top 500
```

### Live intraday catch-up (every hour, 24/7 via Task Scheduler)
The `Schwab-Live` cron fires every hour and grabs the last 2h of 1m bars for the SP500 universe. No daemon, no long-running process — each firing is self-contained and idempotent. Missed runs are caught up by the next firing within Schwab's 48-day 1m lookback.

```bash
# Default: SP500, last 2 hours of 1m
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/schwab/us_equities_schwab_live.py

# Wider catchup window (e.g. recovering after a gap):
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/schwab/us_equities_schwab_live.py --hours 24

# Smaller universe for testing:
"C:/Users/arjd2/.conda/envs/factorlab/python.exe" scripts/us/equities/schwab/us_equities_schwab_live.py --universe watchlist --limit 5
```

**Why a cron, not a daemon?** Schwab's 2 req/sec safe rate means a single pass over SP500 takes ~4 minutes — too long for per-minute polling, and a long-running daemon with internal sleep loop adds crash/restart complexity for no latency win. An hourly cron with 2h overlap delivers the same data freshness in practice (1m bars don't need sub-hourly catchup), and the cron model is self-healing on Task Scheduler restart.
