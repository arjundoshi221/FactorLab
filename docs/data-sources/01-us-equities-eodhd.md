# US Equities — EODHD

> Status: `[active]` — client, instruments, daily candles implemented (April 2026)

## Purpose
Daily price bars, adjusted history, fundamentals, corporate actions, and reference data for US-listed equities and ETFs. Phase 1 anchor source for fundamentals and corporate actions. Also covers global equities (60+ exchanges) at no extra cost.

## Pricing (as of April 2026)

| Plan | Monthly | Annual | Key Features |
|------|---------|--------|-------------|
| Free | $0 | $0 | 20 calls/day, 1yr history, demo tickers only |
| EOD All World | $19.99 | $199 | 100K calls/day, 30yr+ EOD, splits/dividends |
| EOD + Intraday | $29.99 | $299.90 | + intraday (1m/5m/1h), WebSocket (US only) |
| **Fundamentals** | **$59.99** | **$599.90** | + full financials, insider trades, screener |
| All-In-One | $99.99 | $999.90 | Everything: tick data, options, news, bonds |

**No per-exchange fees.** All 60+ exchanges included in every paid plan.

**API call costs (not 1:1):**
- Most endpoints: 1 call. Fundamentals: **10 calls**. Screener: **5 calls**. Bulk exchange: **1 call**.
- 100K daily budget → 500-stock fundamentals refresh = 5,000 calls (5% of budget)

**Recommended plan:** Fundamentals ($60/mo) for Phase 1. Upgrade to All-In-One ($100/mo) when intraday/tick/options needed.

## Source
**EOD Historical Data (eodhd.com)** — REST API, JSON responses, all major US exchanges (NYSE, NASDAQ, AMEX, OTC).

## Auth
- API key in `.env` as `EODHD_API_KEY`
- Demo key `demo` works for `AAPL.US`, `TSLA.US`, `VTI.US`, `AMZN.US` only — useful for unit tests
- All requests carry `?api_token=<key>` query parameter

## Rate limits

### Hard limits
| Limit | Value | Scope |
|-------|-------|-------|
| Requests per minute | **1,000** | All plans |
| Daily API calls (free) | 20 | Resets midnight GMT |
| Daily API calls (paid) | **100,000** | Resets midnight GMT |

### Call cost multipliers (not 1:1!)
| Endpoint | Cost per request |
|----------|-----------------|
| EOD, live, dividends, splits | 1 call |
| Technical indicators | **5 calls** |
| Fundamentals, options, bonds | **10 calls** |
| Screener | **5 calls** |
| Bulk exchange (all tickers) | **100 calls** |
| Bulk with specific symbols | 100 + N calls |

### Budget math (500-stock US universe)
- Daily EOD pull (bulk): 1 call (returns entire exchange)
- Daily incremental (per-symbol): 500 calls
- Weekly fundamentals refresh: 5,000 calls (500 × 10)
- **Headroom:** ~94,500 calls/day remaining

### Response headers
- `X-RateLimit-Remaining` — monitor this to avoid 429s

### Adapter requirements
- Token-bucket rate limiter (~16 req/sec safe ceiling)
- Exponential backoff on HTTP 429
- Daily-quota awareness (track call costs, not just request count)
- Spread requests evenly — bursts near 1,000/min trigger throttling

## Endpoints (subset)
| Endpoint | Use |
|----------|-----|
| `/api/eod/{symbol}` | End-of-day OHLCV history |
| `/api/fundamentals/{symbol}` | Full fundamentals dump (10 calls) |
| `/api/exchange-symbol-list/US` | Universe of US-listed tickers |
| `/api/exchanges-list` | All exchanges |
| `/api/dividends/{symbol}`, `/api/splits/{symbol}` | Corporate actions |
| `/api/calendar/earnings` | Earnings calendar |

Symbol format: `{TICKER}.{EXCHANGE}` — e.g. `AAPL.US`, `BRK-B.US`. The `.US` suffix maps to the consolidated US tape.

## Schema mapping (vendor → canonical)

EODHD daily bar response:
```json
{ "date": "2026-04-24", "open": ..., "high": ..., "low": ...,
  "close": ..., "adjusted_close": ..., "volume": ... }
```

Maps to `market.candles_daily`:

| EODHD field | Canonical column |
|-------------|------------------|
| `date` | `trade_date` |
| `open`/`high`/`low`/`close` | `open`/`high`/`low`/`close` |
| `adjusted_close` | `adj_close` |
| `volume` | `volume` |
| (request time) | `as_of_time` |
| `'eodhd'` | `source` |

`instrument_id` resolved via `ref.instruments` lookup on `instrument_key = 'AAPL.US'`.

## Implemented modules

| Module | Path | Purpose |
|--------|------|---------|
| Client | `src/factorlab/sources/eodhd/client.py` | Rate-limited HTTP client, API key auth via `EODHD_API_KEY` |
| Instruments | `src/factorlab/sources/eodhd/instruments.py` | Fetch `/exchange-symbol-list/US` → `ref.instruments` upsert |
| Candles | `src/factorlab/sources/eodhd/candles.py` | Fetch `/eod/{symbol}` → clean DataFrame |
| Ingest | `src/factorlab/storage/ingest.py:write_candles_daily()` | DataFrame → `market.candles_daily` upsert |
| Demo script | `scripts/factlab_us_daily.py` | CLI: fetch bars, print summary, save Parquet, optionally write DB |

### Free-tier constraints
- 20 API calls/day (resets midnight GMT)
- Demo tickers (`AAPL.US`, `TSLA.US`, `AMZN.US`, `VTI.US`) work without burning quota
- Script prints calls used vs budget remaining

## Pipeline

```
scripts/factlab_us_daily.py [--demo | --symbols SYM1 SYM2] [--db]
        │
        ▼
load universe (configs/universes/us.yaml or --symbols)
        │
        ▼
for each symbol:
    GET /api/eod/{symbol}?from=<from_date>
        │
        ▼
    parse → DataFrame (trade_date, OHLCV, adj_close, source='eodhd')
        │
        ▼
    save Parquet → data/eodhd/{SYMBOL}.parquet
        │
        ▼
    [--db] upsert → market.candles_daily (ON CONFLICT DO NOTHING)
```

## Storage
- Cache: `data/eodhd/*.parquet` (local Parquet per symbol)
- Raw audit: `market.raw_responses` (jsonb payload + url + fetched_at)
- Fact: `market.candles_daily` (TimescaleDB hypertable, chunk=1month)
- Reference: `ref.instruments` (keyed on `instrument_key = 'AAPL.US'`)

## Edge cases & gotchas
- **Adjusted vs. raw close** — EODHD adjusts retroactively for splits/dividends. Always store `close` (raw) AND `adj_close`. Never overwrite raw.
- **Restated history** — corporate actions trigger silent rewriting of `adjusted_close` for the entire history. Snapshot `as_of_time` per ingest is critical.
- **Mid-day pulls** — same-day bar may be partial. Adapter should treat today's bar as "tentative" and re-fetch on next run until it's stable.
- **Missing fundamentals on free tier** — guard with feature flag; don't break ingestion if fundamentals call returns 402/403.
- **Symbol changes** — ticker mergers/renames must trigger a `ref.security_aliases` row insert, not an update of the existing row.
- **Half-day sessions** — handled by exchange calendars (use `exchange_calendars` lib).

## Open questions
- [x] Universe file: hard-coded list, or queried from `/api/exchange-symbol-list/US`? → Both. Demo/mega_cap in `configs/universes/us.yaml`; full exchange list via `fetch_us_instruments()`.
- [ ] How often to re-pull full history vs. incremental? (default: incremental daily; full on schema change or vendor restatement notification)
- [ ] Fundamentals storage cost — at 10 calls each, full quarterly refresh of 500 names is 5,000 calls. Requires Fundamentals plan ($60/mo).
- [ ] Upgrade path: free → EOD All World ($20/mo) → Fundamentals ($60/mo) as usage grows.
