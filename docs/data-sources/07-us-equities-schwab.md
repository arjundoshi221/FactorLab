# US Equities — Charles Schwab

> Status: `[auth implemented; data ingestion pending]`

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

## Auth

### Overview
- **OAuth 2.0 Authorization Code Grant** using the existing Cloudflare broker-auth Workers
- Access token: **30 minutes**, refreshed by the protected Worker during the VPS secret poll
- Refresh token: **7 days** (must re-login via browser + MFA weekly)
- The refresh token remains AES-256-GCM encrypted in Workers KV and never reaches the VPS
- The short-lived access token is rendered only into the US container's tmpfs secret volume
- Redirect URI: `https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/schwab/callback`

### Token endpoints
```
Authorize: https://api.schwabapi.com/v1/oauth/authorize
Token:     https://api.schwabapi.com/v1/oauth/token
```

### Runtime credentials

`SCHWAB_APP_KEY` and `SCHWAB_APP_SECRET` are Cloudflare Secrets Store bindings.
The VPS receives `SCHWAB_ACCESS_TOKEN` from the protected runtime-secrets
endpoint. No Schwab credential or refresh token is stored in `.env`.

### Setup steps (one-time)
1. Open Schwab brokerage at schwab.com (deposit $1)
2. Sign NYSE/NASDAQ/OPRA exchange agreements on schwab.com
3. Register at `https://beta-developer.schwab.com/` with Schwab login
4. Create app:
   - API Product: **"Accounts and Trading Production"**
   - Order Limit: **120 requests/minute**
   - Callback URL: **`https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/schwab/callback`** (must match exactly)
5. Wait for approval: status goes from "Approved - Pending" → "Ready For Use" (1-3 days, up to 2-3 weeks)
6. Save App Key + App Secret in Cloudflare Secrets Store (secret shown only once)
7. Bind both secrets to the protected and public callback Workers and deploy them
8. Open the protected FactorLab broker-auth page and choose **Authenticate Schwab**

### Python client

The repository session automatically adopts a refreshed tmpfs token before
each request. Safe reads are retried once if a token rotates after an HTTP 401;
orders and other mutating requests are never retried automatically.

```python
from factorlab.sources.schwab import get_session

client = get_session()
response = client.get(
    "https://api.schwabapi.com/marketdata/v1/quotes",
    params={"symbols": "AAPL,MSFT"},
)
response.raise_for_status()
```

### Auth gotchas
- **"401 Unauthorized" / "assertion_rejected"** — app is still in "Approved - Pending" state. Wait for "Ready for Use".
- **Callback mismatch** — Schwab requires the registered callback to match the Worker URL exactly.
- **No access token in tmpfs** — inspect the protected management page; `reauth_required` means the seven-day refresh-token lifetime elapsed.
- **Cloud/headless** — unattended access-token refresh works for seven days; full reauthentication still requires a browser and MFA.
- **Weekend re-auth** — refresh token expires after exactly 7 days. Re-authenticate on weekends to avoid Monday morning failures.

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
- The FactorLab session does not auto-retry on 429 — implement backoff in the data client

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
| `GET /marketdata/v1/quotes?symbols=AAPL,MSFT` | Real-time/delayed quotes | Batch up to ~50 symbols |
| `GET /marketdata/v1/pricehistory?symbol=AAPL` | Historical OHLCV bars | Daily 20yr+, minute ~30-60 days |
| `GET /marketdata/v1/chains?symbol=AAPL` | Options chains + Greeks | Full US options |
| `GET /marketdata/v1/instruments?symbol=AAPL&projection=fundamental` | Basic fundamentals | P/E, EPS, div yield, market cap |
| `GET /marketdata/v1/instruments?symbol=Apple&projection=symbol-search` | Instrument search | Symbol/name lookup |
| `GET /marketdata/v1/movers/$SPX.X` | Market movers | S&P 500, Dow, NASDAQ |
| `GET /marketdata/v1/markets?markets=equity` | Market hours | Trading calendar |

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

Symbol format: plain tickers — `AAPL`, `MSFT`, `BRK-B`. No exchange suffix.

## Schema mapping (vendor → canonical)

Schwab price history response:
```json
{"candles": [{"open": ..., "high": ..., "low": ..., "close": ..., "volume": ..., "datetime": 1714003200000}]}
```

Maps to `market.price_bars_daily`:

| Schwab field | Canonical column |
|-------------|------------------|
| `datetime` (epoch ms) | `trade_date` (convert from epoch) |
| `open`/`high`/`low`/`close` | `open`/`high`/`low`/`close` |
| N/A | `adj_close` (Schwab returns adjusted by default) |
| `volume` | `volume` |
| (request time) | `as_of_time` |
| `'schwab'` | `source` |

`security_id` resolved via `ref.security_aliases` with `(vendor='schwab', vendor_id='AAPL')`.

## What Schwab provides that others don't
- **20+ year daily history** for free (EODHD needs paid plan for >1yr)
- **Real-time quotes** for free (IBKR needs $10/mo subscription)
- **Options chains** with Greeks for free
- **Basic fundamentals** (P/E, EPS, market cap, div yield) at no cost

## What Schwab does NOT provide
- Full financial statements (income, balance sheet, cash flow) — use EODHD
- Corporate actions API (splits, dividends history) — use EODHD
- Intraday history beyond ~30-60 days — use IBKR
- Non-US market data — use EODHD/IBKR
- Bulk download (entire exchange) — use EODHD
- Level 2 / depth of book — use IBKR

## Edge cases & gotchas
- **Weekly re-auth required** — 7-day refresh token. Must complete browser OAuth + MFA every week.
- **No headless auth** — browser interaction mandatory. Railway callback pattern recommended.
- **Schwab returns adjusted prices by default** — splits already applied. Compare carefully with EODHD raw prices.
- **App approval delays** — 1-3 days typical, can take 2-3 weeks. Apply early.
- **Encrypted account IDs** — API returns hashed account numbers, not your actual number.
- **Intraday data is shallow** — only ~30-60 days of minute bars. Not suitable for historical intraday research.
- **Post-TDA migration** — the API is the successor to TD Ameritrade's API. Some endpoints behave differently from TDA docs.
- **Exchange agreements** — must sign NYSE/NASDAQ/OPRA agreements on schwab.com before real-time data flows.

## Open questions
- [ ] Use Schwab as primary for US daily backfill (free, deep) and EODHD only for fundamentals?
- [ ] Streaming: worth setting up WebSocket for real-time quotes, or just snapshot polling?
