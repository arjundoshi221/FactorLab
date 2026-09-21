# 002 — Live US market data via Schwab streaming + REST tier

> Status: `[accepted]` — decided 2026-05-08. Implementation pending. Depends on [001](001-local-docker-canonical-db.md).

## Decision

Live intraday US market data (regular + extended hours) is captured via **Schwab WebSocket streaming** for the watchlist + SPX, with **Schwab REST 5-min polling** for the long tail. EODHD is used the next morning IST for fundamentals, corp actions, and gap-fill verification. IBKR is used for periodic validation only.

The PC stays on overnight IST (US market hours) to support this. Local-Docker DB is canonical; no cloud staging.

## Why "live" forces this shape

US regular + extended sessions in IST:

| US session | ET | UTC (EDT) | IST |
|---|---|---|---|
| Pre-market | 04:00–09:30 | 08:00–13:30 | 13:30–19:00 |
| Regular | 09:30–16:00 | 13:30–20:00 | 19:00–01:30 |
| Post | 16:00–20:00 | 20:00–00:00 | 01:30–05:30 |

Live means PC awake through ~19:00–05:30 IST weekdays. That requirement drives most of the resilience checklist below.

## Universe tiering

| Tier | Universe | Method | Cadence | Rationale |
|---|---|---|---|---|
| 1 | watchlist (~50 names) | Schwab WebSocket L1 streaming | sub-second | active research focus, minimum-latency requirement |
| 2 | SPX 500 | Schwab WebSocket L1 streaming | sub-second | fits in one streaming session, no quota cost per quote |
| 3 | R3K-minus-SPX (~2,500 names) | Schwab REST | 5-min poll | streaming may exceed per-session symbol cap; long tail tolerates 5-min |
| 4 | EOD bars + fundamentals (full universe) | EODHD | next-morning 09:00 IST | catches corp actions, splits, official close prices |
| 5 | IBKR validation | IBKR `ib_async` | 50 random names/day | bias check vs Schwab; never the source of record |

**Streaming is the headline change** vs the prior "watchlist live 1m" REST polling: sub-second data, no per-request quota cost, persistent session.

## Schedule (IST, weekdays)

| Time IST | Job | Notes |
|---|---|---|
| 09:00 | `factlab_us_eod` | EODHD pulls prior US session fundamentals + corp actions; reconciles with stream-captured intraday for the same session |
| 09:00–15:30 | India ingest (existing) | unrelated, runs concurrently |
| 17:30 | `factlab_us_session_prep` | refresh Schwab access token, validate streaming auth, warm IBKR if used |
| 18:00 (DST) / 19:00 | streaming WebSocket connects | covers US pre-market open through post-market close |
| Through 02:30 (DST) / 01:30 | streaming stays open | regular + post-market |
| 03:00 | `factlab_us_session_end` | clean disconnect, write session metadata, REST gap-fill missed bars |
| Sun 18:30 | `us_equities_schwab_auth` reminder | weekly Schwab refresh-token re-auth via Railway (manual browser step) |
| Sun 22:00 UTC | `factlab_backup_pg` | weekly `pg_dump` to `E:\` |

## Schwab auth

7-day refresh token + 30-min access token. Manual weekly re-auth is acceptable — same operational pattern as Upstox.

- **Within-week**: streaming session auto-refreshes access token off the refresh token (no human intervention, no fallback needed).
- **Weekly**: Sunday IST evening, run `us_equities_schwab_auth` → browser opens via Railway `/schwab/callback` → user pastes the auth code → Railway exchanges for tokens → local script fetches via `X-Auth-Pin`.
- See [001](001-local-docker-canonical-db.md#auth-pattern-locked-applies-to-every-oauth-source) for the unified Railway auth pattern.

## Resilience checklist (live-mode requirements)

These are non-negotiable for unattended overnight operation. Each item maps to a real failure mode that costs real data when skipped.

| # | Item | Why |
|---|---|---|
| 1 | UPS on the workstation (~$80–120, 1500VA class) | One outage = full session of streaming data lost |
| 2 | Windows power plan: never sleep on AC during 17:00–04:00 IST. Disable hybrid sleep. | Default Windows behaviour kills the streaming session |
| 3 | `powercfg /requestsoverride PROCESS python.exe SYSTEM` | Streaming process actively prevents sleep |
| 4 | Windows Update active hours = 17:00–04:00; defer feature updates | Auto-reboot mid-session is the most common live-data loss mode |
| 5 | Streaming process auto-restart on crash (Task Scheduler "If task fails, restart every 1 min, max 3") + 5-min health-check cron | Process death without restart = silent data loss |
| 6 | On-host write buffer (Arrow ring → batched DB inserts every N seconds) | If Docker hiccups, ticks queue on disk instead of dropping |
| 7 | Gap detection: every 1 min compare last-bar timestamp in `market_us.candles_intraday` to wall clock; if stale >2 min, trigger REST gap-fill | Catches WebSocket silent drops, which Schwab does |
| 8 | Heartbeat alert (Telegram/email) if streaming process stops writing for >5 min | Otherwise you find out at 09:00 IST that 7 hours are empty |

Token refresh is **not** on this list — handled by `schwab-py`, manual weekly only.

## Cost reality

| Item | Cost |
|---|---|
| Electricity, PC on ~9 hrs/night × 22 weekdays/mo @ ~100W | ~$8–12/mo |
| UPS (one-time) | $80–120 |
| Railway (auth server only, smallest tier) | $5/mo |
| Local Docker DB | $0 |
| Cloud cold backup (~10 GB B2, optional) | $0.05/mo |
| **Total ongoing** | **~$15/mo + electricity** |

Cheaper than the prior cloud-DB tier even with overnight PC, and faster on writes by orders of magnitude.

## What this deliberately *does not* include

- **No 1-min REST polling for watchlist or SPX** — streaming replaces it. Quota waste, latency penalty, no benefit.
- **No streaming for the R3K long tail** — REST 5-min is fine; streaming session symbol cap not worth burning.
- **No Railway worker as a "live data fallback"** — local + UPS + sleep-policy is the answer. Railway DB stays off the table per [001](001-local-docker-canonical-db.md).
- **No IBKR as primary live source** — TWS/Gateway is fragile for unattended use. Validation only.
- **No live-mode dependency on EODHD intraday** — EODHD is for next-morning EOD reconciliation only.

## Alternative considered, rejected for now

**Railway live worker, local canonical**: small Railway worker keeps WebSocket session and writes to a Railway *staging* DB; local PC merges staging → canonical when awake. Adds complexity and ~$10/mo. Rejected up-front because (a) Railway-DB is off the table per [001](001-local-docker-canonical-db.md), and (b) UPS + sleep policy + auto-restart should make local reliable enough.

Revisit only if local proves to lose ≥2 sessions/month after the resilience checklist is fully implemented.

## Files affected (when implemented)

- `scripts/factlab_us_stream.py` — new, Schwab WebSocket streaming daemon
- `scripts/factlab_us_live_5min.py` — new, REST 5-min poller for R3K-minus-SPX
- `scripts/factlab_us_eod.py` — new (or extension of existing), 09:00 IST EODHD pull
- `scripts/factlab_us_session_prep.py` — new, 17:30 IST pre-flight
- `scripts/factlab_us_session_end.py` — new, 03:00 IST disconnect + gap-fill
- `scripts/us_equities_schwab_auth.py` — extend for weekly Railway-callback flow
- `scripts/in/equities/upstox/india_equities_upstox_auth_server.py` — add `/schwab/callback` + `/schwab/token` routes (Railway); rename to a vendor-agnostic auth server when US Schwab routes land — see [008](008-script-naming-india-historical.md)
- `src/factorlab/sources/schwab/stream.py` — new, WebSocket session + write-buffer
- `src/factorlab/sources/schwab/poller.py` — new, REST 5-min batch
- `src/factorlab/storage/ingest.py` — add buffered batch-insert for streaming ticks
- [docs/data-sources/us/schwab.md](../data-sources/us/schwab.md) — update with streaming + tier model
- [docs/operations/orchestrators.md](../operations/orchestrators.md) — add the new live-mode jobs

## Open questions

- **Schwab streaming per-session symbol cap.** Need to confirm via `schwab-py` docs whether 550 symbols (SPX + watchlist) fits in one session, or if it must split.
- **R3K membership source.** Currently the R3K universe definition lives where? If not yet defined, [docs/developments/](.) needs a sub-decision on the membership source (EODHD index constituents API vs IBKR vs manual).
- **Heartbeat channel.** Telegram bot vs SMTP email vs PowerShell toast notification — explicit pick owed.
- **Gap-fill upsert strategy.** REST-fetched bars may differ from streaming-aggregated bars by ≤1 tick. Need a deterministic merge rule (default proposal: REST bars overwrite streaming-aggregated bars for the same minute, since REST is the vendor's authoritative aggregation).
