# US Equities — EODHD

> Status: `[scaffold]`

## Purpose
Daily price bars, adjusted history, fundamentals, corporate actions, and reference data for US-listed equities and ETFs. Phase 1 anchor source.

## Source
**EOD Historical Data (eodhd.com)** — REST API, JSON responses, all major US exchanges (NYSE, NASDAQ, AMEX, OTC).

## Auth
- API key in `.env` as `EODHD_API_KEY`
- Demo key `demo` works for `AAPL.US`, `TSLA.US`, `VTI.US`, `AMZN.US` only — useful for unit tests
- All requests carry `?api_token=<key>` query parameter

## Rate limits
- Plan-dependent. Free tier: 20 calls/day (functionally a smoke-test budget).
- Paid plans count fundamentals at **10 API calls each** — budget accordingly.
- Adapter must implement: token-bucket rate limiter, exponential backoff on 429, daily-quota awareness.

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

Maps to `market.price_bars_daily`:

| EODHD field | Canonical column |
|-------------|------------------|
| `date` | `trade_date` |
| `open`/`high`/`low`/`close` | `open`/`high`/`low`/`close` |
| `adjusted_close` | `adj_close` |
| `volume` | `volume` |
| (request time) | `as_of_time` |
| `'eodhd'` | `source` |

`security_id` resolved via `ref.security_aliases` lookup keyed on `(vendor='eodhd', vendor_id='AAPL.US')`.

## Pipeline

```
schedule (daily 22:00 UTC, after US close)
        │
        ▼
list current universe (configs/settings.yaml: eodhd_us_universe)
        │
        ▼
for each symbol:
    GET /api/eod/{symbol}?from=<last_trade_date>
        │
        ▼
    write raw payload → market.raw_eodhd
        │
        ▼
    parse → market.price_bars_daily (UPSERT on PK)
        │
        ▼
    record ingest_log row
```

## Storage
- Raw: `market.raw_eodhd` (jsonb payload + url + fetched_at)
- Fact: `market.price_bars_daily` (partitioned yearly)
- Reference: `ref.securities`, `ref.security_aliases (vendor='eodhd')`

## Edge cases & gotchas
- **Adjusted vs. raw close** — EODHD adjusts retroactively for splits/dividends. Always store `close` (raw) AND `adj_close`. Never overwrite raw.
- **Restated history** — corporate actions trigger silent rewriting of `adjusted_close` for the entire history. Snapshot `as_of_time` per ingest is critical.
- **Mid-day pulls** — same-day bar may be partial. Adapter should treat today's bar as "tentative" and re-fetch on next run until it's stable.
- **Missing fundamentals on free tier** — guard with feature flag; don't break ingestion if fundamentals call returns 402/403.
- **Symbol changes** — ticker mergers/renames must trigger a `ref.security_aliases` row insert, not an update of the existing row.
- **Half-day sessions** — handled by exchange calendars (use `exchange_calendars` lib).

## Existing code (prior to docs)
Per memory, prior repo states had:
- `src/factorlab/api/eodhd/parsers.py` — vendor field mapping
- `src/factorlab/compute/metrics.py` — ROIC, FCF, margins
- `src/factorlab/compute/quality.py` — Sloan accruals

When restoring/rewriting, port these against the new Postgres schema rather than the prior Parquet/LocalStore setup.

## Open questions
- [ ] Universe file: hard-coded list, or queried from `/api/exchange-symbol-list/US`? (likely the latter, with manual curation overrides)
- [ ] How often to re-pull full history vs. incremental? (default: incremental daily; full on schema change or vendor restatement notification)
- [ ] Fundamentals storage cost — at 10 calls each, full quarterly refresh of 500 names is 5,000 calls. Plan tier dependency.
- [ ] Do we want intraday bars from EODHD, or use IBKR for US intraday once it's wired up?
