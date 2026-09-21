# Environment variables — canonical reference

Single source of truth for every env var FactorLab reads. The repo's `.env.example` should mirror this file; if they drift, this file wins.

**Owner:** faraday · **Last updated:** 2026-05-13 (Phase 1)

---

## Path knobs

The repo (`<repo>/data/...`) is the canonical / primary location. The application never reads from NAS at runtime — NAS is a backup-only destination, see `docs/operations/nas-storage.md`.

```
FACTORLAB_RAW_ROOT=                          # default: <repo>/data         (canonical; do NOT flip to NAS)
FACTORLAB_STATE_ROOT=                        # default: <repo>/data/_state  (per-source resume checkpoints)
FACTORLAB_TOKEN_ROOT=                        # default: <repo>/data         (Upstox / Schwab token files)
FACTORLAB_LOG_ROOT=                          # default: <repo>/logs
FACTORLAB_HEARTBEAT_ROOT=                    # default: <repo>/data/_heartbeat
FACTORLAB_NAS_BACKUP_ROOT=E:/NAS/factorlab/raw  # destination for scripts/_shared/sync_raw_to_nas.py only
```

Everything in code is resolved through `factorlab.shared.paths`. Hardcoded `data/...raw` literals in source / scripts are blocked by `scripts/_check_paths_in_code.py`.

`FACTORLAB_NAS_BACKUP_ROOT` is deliberately distinct from `FACTORLAB_RAW_ROOT` so an operator cannot accidentally relocate the working data set.

## Core data

```
DATABASE_URL=                                # Postgres (Railway). Required.
```

## US equities — Schwab

```
SCHWAB_APP_KEY=
SCHWAB_APP_SECRET=
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
SCHWAB_TOKEN_PATH=                           # optional; default routes via paths.token_path("schwab")
```

## US equities — EODHD

```
EODHD_API_KEY=                               # real key in .env; never default to "demo" in code
```

## US political — API keys (used by political backfill / daily)

```
FEC_API_KEY=                                 # primary key in FEC rotation pool
CONGRESS_API_KEY=                            # primary key for Congress.gov rotation pool
BACKUP_CONGRESS_FEC_API_KEY=                 # cross-federated fallback (FEC + Congress.gov)
BACKUP_CONGRESS_API_KEY=                     # legacy Congress.gov backup (kept for compat)
FINNHUB_API_KEY=                             # Finnhub gov-contracts feed
LDA_API_TOKEN=                               # Lobbying Disclosure Act bearer token (optional; 15 req/min without)
FACTORLAB_USER_AGENT=FactorLab/1.0           # outbound UA — NEVER include personal email
```

## India equities — Upstox

```
UPSTOX_API_KEY=                              # OAuth client_id
UPSTOX_API_SECRET=                           # OAuth client_secret
UPSTOX_REDIRECT_URL=                         # OAuth redirect URI (matches Railway auth-server)
UPSTOX_ACCESS_TOKEN=                         # ephemeral token (Railway-fetched preferred)
AUTH_SERVER_URL=                             # Railway base URL for OAuth callback
AUTH_SERVER_PIN=                             # shared PIN for /login gate
FLASK_SECRET_KEY=                            # pin to env on Railway (no fallback)
PORT=8888                                    # auth server port
```

## Notifications (Phase 4)

```
FACTORLAB_NOTIFY_BACKEND=outlook             # outlook | smtp | telegram | jsonl | null | comma-separated
FACTORLAB_NOTIFY_TO=                         # recipient address (env only — never appears in code)
FACTORLAB_NOTIFY_PIN=                        # shared secret with notifier daemon
FACTORLAB_NOTIFY_URL=http://127.0.0.1:8765/alert
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## Audit notes (2026-05-13)

- `BACKUP_CONGRESS_API_KEY` and `BACKUP_CONGRESS_FEC_API_KEY` are intentionally separate; they are used as cross-federated fallbacks in the FEC and Congress.gov clients respectively.
- `FACTORLAB_USER_AGENT` defaults to `"FactorLab/1.0"` in code. Never override with a value that includes a personal email — see [`memory/projects/factorlab/decisions.md`](file:///E:/AGENTS/memory/projects/factorlab/decisions.md) and the `feedback_no_email_leakage` rule.
- `EODHD_API_KEY` legacy fallback of `"demo"` exists in code but real `.env` always has a key — never trip the demo branch in scheduled runs.

## How to apply changes

1. Edit `.env.example` to mirror this file. Heimdall reviews any new credential entry.
2. Update this file in the same commit (`docs/operations/env-reference.md`).
3. Faraday signs off — env hygiene is his lane.
