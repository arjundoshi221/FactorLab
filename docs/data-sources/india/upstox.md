# India Markets — Upstox

> Status: `[active]` — client + stream built, ported from working reference

## Purpose
NSE / BSE / MCX coverage: equities, equity F&O, index F&O, commodity F&O, currency F&O, indices. Daily and minute bars.

## Source
**Upstox API** — `https://api.upstox.com/`. Both V2 and V3 are free with the same OAuth token (confirmed live April 2026).

---

## 1. Authentication

**OAuth2 flow — token valid daily, expires ~4:30 AM IST next day.**

### Manual token generation
1. Go to https://account.upstox.com/developer/apps
2. Generate token from developer dashboard

### Programmatic OAuth2 flow
```
Step 1 — Open in browser:
https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=<API_KEY>&redirect_uri=http%3A%2F%2Flocalhost%3A8888%2F

Step 2 — After login, browser redirects to:
http://localhost:8888/?code=XXXXXX
(copy the code= value from URL bar — page will show connection error, that's fine)

Step 3 — Exchange code for token:
POST https://api.upstox.com/v2/login/authorization/token
Body (form-urlencoded):
  code=XXXXXX
  client_id=<API_KEY>
  client_secret=<API_SECRET>
  redirect_uri=http://localhost:8888/
  grant_type=authorization_code

Returns JSON with access_token
```

### Credentials (.env)
```
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
UPSTOX_REDIRECT_URL=http://localhost:8888/
UPSTOX_ACCESS_TOKEN=...
UPSTOX_AUTH_CODE=...
```

### Header on every API call
```
Authorization: Bearer <UPSTOX_ACCESS_TOKEN>
Accept: application/json
```

### Token validation endpoint
```
GET https://api.upstox.com/v2/user/profile
```
Returns `{"status":"success","data":{"user_name":"...","user_id":"...","broker":"...","exchanges":["NSE","BSE","MCX"],...}}`

---

## 2. Rate Limits

All standard APIs (historical candle, profile, holdings, etc.) share the same bucket:

| Window | Limit |
|--------|-------|
| Per second | 50 requests |
| Per minute | 500 requests |
| Per 30 minutes | 2,000 requests |

Order placement APIs are separate (10 req/sec unregistered, 50 req/sec SEBI-registered).

**Exceeding limits:** Returns `"Too Many Requests Sent"` error — temporary suspension of access.

**Practical implications for bulk data pulls:**
- 50 stocks × 12 monthly calls each = 600 requests → ~12 seconds at max rate
- Safe practice: add `0.05s` sleep between calls, batch in groups of 100
- For a full universe pull (500 stocks × 1yr of 1min data = 6,000 calls) → takes ~2 min at max rate

**Rate limit is per-user, per-API. No way to increase it (no paid tier for limits).**

---

## 3. API Endpoints

**Base URL:** `https://api.upstox.com/`
**Pricing: Both V2 and V3 are free** — same token, no extra subscription. Confirmed live April 2026.

### V3 Historical candles (preferred — use this)

```
GET https://api.upstox.com/v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}
```

**URL construction — explicit breakdown (V3):**
```
https://api.upstox.com               ← base domain
/v3                                  ← API version
/historical-candle                   ← endpoint
/NSE_EQ|INE002A01018                 ← instrument_key  (pipe | unencoded, Upstox accepts raw)
/minutes                             ← unit  (minutes / hours / days / weeks / months)
/1                                   ← interval number  (1 = 1-minute bars)
/2026-04-24                          ← to_date   ← END date first
/2026-04-24                          ← from_date ← START date second
```

**Parameters:**

| Parameter | Values | Notes |
|-----------|--------|-------|
| `unit` | `minutes`, `hours`, `days`, `weeks`, `months` | |
| `interval` | 1–300 (minutes), 1–5 (hours), 1 (days/weeks/months) | |
| `to_date` | `YYYY-MM-DD` | end date, inclusive |
| `from_date` | `YYYY-MM-DD` | start date |

**Max data per call (V3):**

| Unit | Interval | Max range per call | History available from |
|------|----------|--------------------|----------------------|
| minutes | 1–15 | 1 month | Jan 2022 |
| minutes | 16–300 | 1 quarter | Jan 2022 |
| hours | 1–5 | 1 quarter | Jan 2022 |
| days | 1 | 1 decade | Jan 2000 |
| weeks | 1 | unlimited | Jan 2000 |
| months | 1 | unlimited | Jan 2000 |

For **1-minute data**: max 1 month per call → 1 year = 12 calls per instrument.

**Confirmed working URLs (tested April 2026):**
```
# Single day — RELIANCE 1-min (375 candles returned)
https://api.upstox.com/v3/historical-candle/NSE_EQ|INE002A01018/minutes/1/2026-04-24/2026-04-24

# Multi-day range pattern
https://api.upstox.com/v3/historical-candle/NSE_EQ|INE040A01034/minutes/1/2026-04-24/2026-04-01
https://api.upstox.com/v3/historical-candle/NSE_FO|66691/minutes/1/2026-04-24/2026-04-01
```

### V2 Historical candles (legacy fallback — still works, ~7 day limit for 1min)

```
GET https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{end_date}/{start_date}
```

**URL construction — explicit breakdown (V2):**
```
https://api.upstox.com          ← base domain
/v2                             ← API version
/historical-candle              ← endpoint
/NSE_EQ|INE002A01018            ← instrument_key
/1minute                        ← interval string (not split like V3)
/2026-04-24                     ← end_date   ← END first
/2026-04-22                     ← start_date ← START second
```

**Valid interval strings (V2):** `1minute`, `30minute`, `day`, `week`, `month`

**V2 vs V3 comparison:**

| | V2 | V3 |
|--|----|----|
| 1-min range per call | ~7 days (undocumented cap) | 1 full month |
| Interval format | `1minute` string | `minutes/1` (unit + number) |
| Custom intervals | No (fixed strings only) | Yes (any 1–300 min) |
| Daily history | Jan 2000 | Jan 2000 |
| Minute history | Jan 2022 | Jan 2022 |
| Free | Yes | Yes |
| Date order in URL | end/start | to/from (same — end first) |

### Intraday candles (today only — no date parameters)
```
GET https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/{interval}
GET https://api.upstox.com/v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}
```
Returns candles from 09:15 to current minute.

### Response format (identical across V2 and V3)
```json
{
  "status": "success",
  "data": {
    "candles": [
      ["2026-04-24T09:15:00+05:30", 1340.0, 1345.9, 1338.6, 1344.0, 828627, 0]
    ]
  }
}
```

Columns: `[timestamp, open, high, low, close, volume, open_interest]`
- Candles returned **newest-first** — reverse the list to get chronological order
- OI is `0` for equities, populated for futures/options
- Timestamp includes IST offset `+05:30`

---

## 4. Instruments Master File

Published by Upstox daily at ~6 AM IST. **No auth needed to download.**

| File | URL |
|------|-----|
| Complete (all exchanges) | `https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz` |
| NSE only | `https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz` |
| BSE only | `https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz` |
| MCX only | `https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz` |
| Mutual Funds | `https://assets.upstox.com/market-quote/instruments/exchange/mf-instruments.json.gz` |
| Suspended | `https://assets.upstox.com/market-quote/instruments/exchange/suspended-instrument.json.gz` |

Files are gzipped JSON arrays. Download and decompress:
```python
import urllib.request, gzip, json
with urllib.request.urlopen(url) as r:
    instruments = json.loads(gzip.decompress(r.read()))
```

---

## 5. Full Indian Market Coverage (April 2026)

**Total: 159,327 instruments across all exchanges**

### By Segment

| Segment | Total | FUT | CE | PE | EQ/INDEX | Description |
|---------|-------|-----|----|----|----------|-------------|
| `NSE_FO` | 50,855 | 646 | 24,998 | 25,211 | — | NSE equity & index F&O |
| `NSE_COM` | 39,314 | 134 | 19,577 | 19,577 | 26 | NSE commodity derivatives |
| `MCX_FO` | 22,470 | 146 | 11,162 | 11,162 | — | MCX commodity F&O |
| `BSE_EQ` | 12,646 | — | — | — | 12,646 | BSE listed equities |
| `BCD_FO` | 9,582 | 88 | 4,747 | 4,747 | — | BSE currency derivatives |
| `NSE_EQ` | 9,262 | — | — | — | 9,262 | NSE equities + bonds + ETFs |
| `NCD_FO` | 9,068 | 92 | 4,488 | 4,488 | — | NSE currency derivatives |
| `BSE_FO` | 5,923 | 9 | 2,957 | 2,957 | — | BSE index F&O (SENSEX, BANKEX) |
| `NSE_INDEX` | 135 | — | — | — | 135 | NSE indices (reference only) |
| `BSE_INDEX` | 72 | — | — | — | 72 | BSE indices (reference only) |

### NSE_EQ Sub-types

| Type | Count | Description |
|------|-------|-------------|
| `EQ` | 2,485 | Mainboard equities |
| `GS` | 131 | Government securities |
| `GB` | 46 | Sovereign gold bonds |
| other | ~6,600 | SME, ETFs, debt instruments |

### Asset Classes Available

| Asset Class | Segment | Type | instrument_key format |
|------------|---------|------|-----------------------|
| NSE stocks | `NSE_EQ` | `EQ` | `NSE_EQ\|{ISIN}` |
| NSE equity futures | `NSE_FO` | `FUT` | `NSE_FO\|{exchange_token}` |
| NSE index futures (NIFTY, BANKNIFTY) | `NSE_FO` | `FUT` | `NSE_FO\|{exchange_token}` |
| NSE equity/index options | `NSE_FO` | `CE`/`PE` | `NSE_FO\|{exchange_token}` |
| BSE stocks | `BSE_EQ` | varies | `BSE_EQ\|{exchange_token}` |
| SENSEX / BANKEX futures | `BSE_FO` | `FUT` | `BSE_FO\|{exchange_token}` |
| MCX commodities (Gold, Crude, etc.) | `MCX_FO` | `FUT` | `MCX_FO\|{exchange_token}` |
| NSE commodities | `NSE_COM` | `FUT` | `NSE_COM\|{exchange_token}` |
| Currency futures (USDINR etc.) | `NCD_FO` | `FUT` | `NCD_FO\|{exchange_token}` |
| NSE indices | `NSE_INDEX` | `INDEX` | `NSE_INDEX\|{name}` |

### MCX Futures Available
Gold, Silver, Crude Oil, Natural Gas, Copper, Zinc, Lead, Aluminium, Nickel + agri contracts (146 contracts total)

### Currency Futures Available (NCD_FO / BCD_FO)
USDINR, EURINR, GBPINR, JPYINR

---

## 6. Instrument Key Format & Lookup

### Key formats

```
NSE_EQ    →  NSE_EQ|{ISIN}              e.g. NSE_EQ|INE040A01034   (HDFCBANK equity)
NSE_FO    →  NSE_FO|{exchange_token}    e.g. NSE_FO|66691          (NIFTY FUT)
MCX_FO    →  MCX_FO|{exchange_token}    e.g. MCX_FO|559933         (GOLD FUT)
NCD_FO    →  NCD_FO|{exchange_token}    e.g. NCD_FO|7009           (USDINR FUT)
NSE_INDEX →  NSE_INDEX|{name}           e.g. NSE_INDEX|Nifty 50
```

### Sample JSON shapes

**NSE_EQ (equity):**
```json
{
  "segment": "NSE_EQ",
  "instrument_type": "EQ",
  "instrument_key": "NSE_EQ|INE040A01034",
  "trading_symbol": "HDFCBANK",
  "name": "HDFC BANK LIMITED",
  "isin": "INE040A01034",
  "exchange_token": "1333",
  "tick_size": 0.05,
  "lot_size": 1,
  "security_type": "NORMAL"
}
```

**NSE_FO FUT (futures):**
```json
{
  "segment": "NSE_FO",
  "instrument_type": "FUT",
  "instrument_key": "NSE_FO|66691",
  "trading_symbol": "NIFTY FUT 28 APR 26",
  "underlying_symbol": "NIFTY",
  "expiry": 1745855399000,
  "lot_size": 65,
  "freeze_quantity": 1950.0,
  "asset_key": null,
  "underlying_key": "NSE_INDEX|Nifty 50",
  "tick_size": 5.0,
  "weekly": false
}
```

**Expiry is Unix timestamp (ms):** `datetime.fromtimestamp(expiry / 1000)`

### Programmatic lookup

```python
import urllib.request, gzip, json
from datetime import datetime

# Download instruments (do once per day, cache locally)
url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
with urllib.request.urlopen(url) as r:
    instruments = json.loads(gzip.decompress(r.read()))

# Equities: exact match on trading_symbol
def get_eq_key(symbol):
    return next(
        (i["instrument_key"] for i in instruments
         if i["segment"] == "NSE_EQ"
         and i["instrument_type"] == "EQ"
         and i["trading_symbol"] == symbol),
        None
    )

# Futures: match underlying_symbol, pick nearest expiry
def get_fut_key(symbol):
    candidates = [
        i for i in instruments
        if i["segment"] == "NSE_FO"
        and i["instrument_type"] == "FUT"
        and i["underlying_symbol"] == symbol
    ]
    candidates.sort(key=lambda x: x["expiry"])
    return candidates[0]["instrument_key"] if candidates else None

get_eq_key("HDFCBANK")   # → NSE_EQ|INE040A01034
get_fut_key("NIFTY")     # → NSE_FO|66691
get_fut_key("BANKNIFTY") # → NSE_FO|...
```

---

## 7. Gotchas & Edge Cases

1. **Date order reversed** — `to_date` (end) comes BEFORE `from_date` (start) in the URL path. Easy to invert and silently get empty results.
2. **Pipe `|` in instrument_key** — pass unencoded. Upstox accepts raw pipe characters.
3. **Same date for single day** — use same date for both `to_date` and `from_date`.
4. **Candles returned newest-first** — reverse the list for chronological order.
5. **Token expiry mid-job** — wrap calls in 401-retry-with-refresh logic; otherwise long-running batches die around 4:30 AM IST.
6. **Future expiries** — futures contracts expire; `instrument_key` for a given underlying changes monthly. Always resolve via instruments file at job start.
7. **Holiday calendar** — NSE has half-days, Muhurat trading sessions, etc. Use `exchange_calendars` with key `XBOM` (NOT `XNSE` — it doesn't exist). NSE and BSE share the same holiday calendar under `XBOM`.
8. **Currency** — INR throughout; mark currency on every fact row to keep US/IN data joinable.
9. **Trading hours** — NSE equity 09:15–15:30 IST. 375 one-minute candles per full session day.
10. **OI field** — always 0 for equities; populated for futures/options.

---

## 8. Pipeline Design

```
daily 04:30 IST  →  refresh access token (manual or scripted browser)
        │
        ▼
06:30 IST  →  download instruments master (NSE.json.gz)
        │
        ▼
        →  reconcile ref.securities + ref.security_aliases
        │
        ▼
post-close 16:00 IST  →  for each instrument in universe:
                          GET historical candles (incremental)
                          → raw_upstox → fact tables
        │
        ▼
weekly  →  pull MCX commodity & currency derivatives history
```

### Data flow
```
.env  →  load ACCESS_TOKEN
        |
settings.yaml  →  universe (symbol, type) pairs
        |
NSE.json.gz  →  instrument_key lookup per symbol
        |
API: /v3/historical-candle/{key}/minutes/1/{to}/{from}
        |
Response: [[timestamp, O, H, L, C, volume, oi], ...]
        |
DataFrame cleanup:
  - reverse to chronological
  - remove weekends
  - deduplicate timestamps
  - filter to 09:15–15:29 IST
        |
Storage: Parquet (local) → Postgres (prod)
```

---

## 9. Smoke Test Results — April 2026

**Date range:** `2026-04-22` to `2026-04-24` | **Interval:** `1minute`

| Instrument | instrument_key | Candles | First | Last |
|-----------|---------------|---------|-------|------|
| HDFCBANK EQ | `NSE_EQ\|INE040A01034` | 1,125 | 22 Apr 09:15 @ 810.0 | 24 Apr 15:29 @ 785.15 |
| NIFTY FUT (28 Apr 26) | `NSE_FO\|66691` | 1,125 | 22 Apr 09:15 @ 24451.6 | 24 Apr 15:29 @ 23929.7 |
| RELIANCE EQ (V3) | `NSE_EQ\|INE002A01018` | 375 | 24 Apr 09:15 | 24 Apr 15:29 |
| RELIANCE EQ (V3 1M range) | `NSE_EQ\|INE002A01018` | ~7,500 | 25 Mar – 24 Apr |

3 trading days × 375 minutes = 1,125 candles per instrument. All returning correctly.

---

## 10. Legacy Reference Code

The original pipeline (now superseded by `src/factorlab/api/upstox/`) lived at a sibling directory with these key patterns worth preserving:

### Authorization flow (uplinkAuthorization.py)
- Opens browser to OAuth dialog
- User pastes `code=` from redirect
- POSTs to `/v2/login/authorization/token`
- Writes `access_token` to file

### Data processing patterns (uplinkDataProcessing.py)
- `clean_df()`: weekday filter, dedup timestamps, sort chronological
- `resample_hourly()`: 60min resample with 15min offset, trading hours 09:15–16:00
- `merge_save_file()`: concat + dedup on timestamp index + sort
- `fetch_and_save_data()`: single ticker fetch → DataFrame → CSV

### Scheduler (scheduler.py)
- APScheduler `BackgroundScheduler` with IST timezone
- Hourly jobs at XX:15 from 10:15 to 15:15, plus 15:28 for close
- Runs `hourly_data_pull.py` via subprocess

### Key patterns to port
- Instruments master download + segment/type filtering
- V3 > V2 for 1-min data (1 month vs ~7 days per call)
- Chronological reversal on response (newest-first → oldest-first)
- Trading hours filter 09:15–15:29
- Rate limit sleep between calls (0.05s minimum)

---

## 11. Developer Portal Links

| Resource | URL |
|----------|-----|
| API documentation | https://upstox.com/developer/api-documentation/ |
| Authentication docs | https://upstox.com/developer/api-documentation/authentication/ |
| Request structure | https://upstox.com/developer/api-documentation/request-structure/ |
| Developer apps (token generation) | https://account.upstox.com/developer/apps |
| Instruments docs | https://upstox.com/developer/api-documentation/instruments/ |

---

## 12. Configuration Architecture

FactorLab separates **what to track** (universes) from **how to fetch** (sources) from **what's available today** (dynamic state):

```
configs/universes/india.yaml     ← symbols, exchanges, calendar keys
configs/sources/upstox.yaml      ← API endpoints, rate limits, auth type
data/upstox/instruments/         ← instruments master (downloaded daily, gitignored)
```

### Universe config (`configs/universes/india.yaml`)
- Defines exchanges with calendar keys (`XBOM`), trading hours, segment/type for instrument resolution
- Universes reference an exchange + symbol list (equities) or underlyings + resolution strategy (futures)
- Futures use `resolution: nearest_expiry` — resolved at runtime from instruments master

### Source config (`configs/sources/upstox.yaml`)
- API base URL, version, auth type, rate limits, endpoint templates
- Data limits per interval (max range per call, history start date)
- Response format (column order, sort direction)

### Dynamic state (`data/upstox/instruments/`)
- Instruments master (gzipped JSON) downloaded daily at ~06:30 IST
- Cached locally; re-downloaded if stale (not today's date)
- Futures instrument_keys resolved from this at job start

### Calendar keys (exchange_calendars library)
- `XBOM` — NSE + BSE (shared holiday calendar). **`XNSE` does not exist.**
- `XBSE` — BSE-specific (rarely needed, same as XBOM in practice)
- `XNYS` — NYSE (for US equities)

### Verified April 2026
- 1-min runner tested with `india:demo` (5 equities) and `india:nifty_futures` (NIFTY + BANKNIFTY nearest expiry)
- 5 trading days × 375 candles = 1,875 per instrument, zero gaps
- Futures auto-resolved: NIFTY → `NSE_FO|66691` (expiry 28 Apr 2026)

---

## 13. Open Questions

- [x] Is intraday minute data needed initially, or daily-only? **→ 1-min. Same API cost as 15-min; max flexibility.**
- [ ] Storage strategy for minute data: Postgres partitioned table vs. Parquet? (1 stock × 1 yr × 1-min ≈ 100k rows; 500 stocks ≈ 50M rows; 5 yrs ≈ 250M)
- [ ] Auto token refresh — feasible without violating Upstox TOS? (Their docs imply manual flow is expected.)
- [ ] India execution venue — Upstox for orders, or use IBKR India?
