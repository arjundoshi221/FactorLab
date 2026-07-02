# US Equities — Charles Schwab

> Status: `[scaffold]`

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
   - API Product: **"Accounts and Trading Production"**
   - Order Limit: **120 requests/minute**
   - Callback URL: **`https://127.0.0.1:8182`** (must match exactly — case, port, no trailing slash)
5. Wait for approval: status goes from "Approved - Pending" → "Ready For Use" (1-3 days, up to 2-3 weeks)
6. Save App Key + App Secret (secret shown only once)

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
- **"401 Unauthorized" / "assertion_rejected"** — app is still in "Approved - Pending" state. Wait for "Ready for Use".
- **Browser SSL warnings** — `client_from_login_flow()` uses self-signed cert for local HTTPS. Safe to ignore if URL matches callback.
- **Never manually edit token files** — `schwab-py` manages the entire lifecycle. Creating/modifying files yourself causes parsing failures.
- **Cloud/headless** — no browser? Create token on desktop first, transfer `token.json` to server. Or deploy Railway callback (same as Upstox).
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

## Library
```bash
pip install schwab-py
```
- GitHub: https://github.com/alexgolec/schwab-py
- Docs: https://schwab-py.readthedocs.io/
- Author: Alex Golec (same as the beloved `tda-api`)

## Open questions
- [ ] Deploy Railway callback server for Schwab OAuth (same pattern as Upstox)?
- [ ] Token refresh cadence: cron job every 25 minutes, or on-demand before each API call?
- [ ] Use Schwab as primary for US daily backfill (free, deep) and EODHD only for fundamentals?
- [ ] Streaming: worth setting up WebSocket for real-time quotes, or just snapshot polling?
