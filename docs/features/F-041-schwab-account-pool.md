---
id: F-041
title: Pool several Schwab developer apps so US collection gets their combined rate budget
status: in-progress
priority: P1
components: [ingest-us, secrets-agent, upstox-auth-worker, upstox-oauth-callback-worker]
owner: unassigned
sprint: null
decisions: []
links: []
created: 2026-10-21
updated: 2026-10-21
---

# F-041 Schwab account pool

## Problem

All Schwab market data goes through one developer app and one OAuth token. That is about
120 requests per minute, shared by the US collector, universe validation and shadow
runs. Full-universe daily and 1-minute recovery is limited by that one budget. A second
Schwab account with its own developer app is available, and more may follow.

## Scope

- Each account has an id. `primary` is today's account and keeps every existing name.
  Any other id `<id>` uses these names:
  - Secrets Store: `SCHWAB_APP_KEY_<ID>` and `SCHWAB_APP_SECRET_<ID>`.
  - KV: `schwab/accounts/<id>/current-token`.
  - Runtime secret: `SCHWAB_ACCESS_TOKEN_<ID>` (+ `.expires_at`).
- Auth Worker:
  - The `SCHWAB_ACCOUNTS` var lists the accounts.
  - Login, refresh and status work per account.
  - The management page has one card per account.
  - `/v1/runtime-secrets` adds `SCHWAB_ACCESS_TOKEN_<ID>` and `schwab_accounts`. Both
    are additive; the legacy `schwab` fields describe `primary`.
- Callback Worker: the sealed state carries the account, and the code is exchanged with
  that account's app credentials.
- secrets-agent:
  - Renders the files for each valid account.
  - Removes the files of invalid and orphaned accounts.
  - Expires suffixed tokens locally.
- Provider: `api.accounts` in `configs/sources/schwab.yaml`. `SchwabTransport` keeps one
  limiter per account and routes each request to the ready account with the most free
  budget.
  - A 429 benches the account for its `Retry-After`.
  - A 401 on an unrotated token benches the account for up to 5 minutes.
  - Either way, the request moves on to another account.
  - `raw.archive` metadata records `schwab_account`.

## Non-goals

- Coordinating budgets across processes. `universe-us` and `ingest-us` each pool all
  accounts independently, as they shared one token before.
- Pinning an account to a service.

## Acceptance criteria

- [x] With `accounts: [primary]`, behaviour and the existing tests are unchanged.
- [x] Requests alternate across ready accounts. Throttled, rejected and expired
      accounts are skipped. When no account is usable, the transport raises
      `AuthRequired` or `RateLimited` without HTTP.
- [x] The agent renders and removes per-account token files. Primary output is unchanged.
- [x] Both Workers type-check and bundle.
- [ ] acct2 authenticated in production, and `raw.archive` shows both accounts serving requests.

## Rollout

Each step is backward compatible.

1. **Owner:**
   - Register `.../oauth/schwab/callback` on the new Schwab app.
   - Add `SCHWAB_APP_KEY_ACCT2` and `SCHWAB_APP_SECRET_ACCT2` to the Secrets Store.
   - Uncomment those bindings in both local `wrangler.toml` files.
   - Set `SCHWAB_ACCOUNTS = "primary,acct2"` on the auth Worker.
2. Deploy `upstox-oauth-callback-worker` and `upstox-auth-worker` by hand, then
   authenticate acct2 on the management page.
3. Release `secrets-agent`.
4. Release `ingest-us`, which carries `api.accounts: [primary, acct2]`.

Every account needs its own login every 7 days. Stagger the logins.

## Log

- 2026-10-21: created; implemented in the working tree.
- 2026-10-29: `SCHWAB_APP_KEY_ACCT2`/`SCHWAB_APP_SECRET_ACCT2` in the Secrets Store; both
  Workers deployed with the acct2 bindings and `SCHWAB_ACCOUNTS = "primary,acct2"`. The
  live secrets-agent (`b54ee228`) and ingest-us (`6e25394`) trials already carry F-041, so
  no release was needed. Waiting on the acct2 login.
