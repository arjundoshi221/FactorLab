# factorlab-provider-schwab

> Charles Schwab Trader API adapter for US per-symbol listing lookups and daily and 1min bars from `/pricehistory`.

## Purpose

This is a provider member. It implements `plan -> fetch -> normalize` ([07 §6](../../docs/architecture/07-ingestion-provider-abstraction.md)) for `ref.listings` and `market.bars` in market `USA`.
Importing `factorlab.sources.schwab` registers `SchwabListings` and `SchwabBars` ([`__init__.py`](src/factorlab/sources/schwab/__init__.py)).
The production US collector uses primary Schwab bindings through the shared engine. Quote reference batches require every configured equity to validate.

## Owns and does not own

- Owns: the market-data transport with bearer auth, token-expiry checks and the account pool ([transport.py](src/factorlab/sources/schwab/transport.py)), unit planning ([sources.py](src/factorlab/sources/schwab/sources.py)), and the pure mapping from Schwab JSON to records ([normalize.py](src/factorlab/sources/schwab/normalize.py)).
- Does not own: the OAuth flow and token refresh (Cloudflare Worker plus secrets agent, [05](../../docs/architecture/05-secrets-and-upstox-auth.md)), the listing master (EODHD is meant to own it), ClickHouse writes (`factorlab-storage` sinks), or the production scheduler (`components/ingest-us`).

## Entry points

- Registered sources: `SchwabListings` (`ref.listings`, one `/instruments` call per symbol) and `SchwabBars` (`market.bars`, `daily` and `1min`).
- Helpers `provider_symbol()` (`BRK-B` -> `BRK/B`) and `canonical_symbol()` in [normalize.py](src/factorlab/sources/schwab/normalize.py).
- Run it through the component: `factorlab-ingest-us engine run --dataset ref.listings --market USA --instance schwab --symbol BRK-B --dry-run`.

## Configuration and secrets

[settings.py](src/factorlab/sources/schwab/settings.py) loads [configs/sources/schwab.yaml](../../configs/sources/schwab.yaml). Keys it does not declare are ignored.

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `api.access_token_env` | `SCHWAB_ACCESS_TOKEN` | same | Bearer token, read through `get_secret` on every request |
| (derived) | `SCHWAB_ACCESS_TOKEN.expires_at` | - | ISO timestamp. If it is missing, unparseable or in the past, the call raises `AuthRequired`. |
| `api.accounts` | `SCHWAB_ACCESS_TOKEN_<ID>` (+ `.expires_at`) for ids other than `primary` | `[primary]` | Pooled Schwab apps; each has its own token and limiter. Accounts without a valid token are skipped. |
| `api.base_url` / `api.timeout` | - | `https://api.schwabapi.com` / 30s | The path prefix is `/marketdata/v1` |
| `rate_limits.requests_per_second` | - | 1.0 | Per account; truncated to an int for a 1s window |
| `rate_limits.requests_per_minute` | - | 120 | Local 60s ceiling; approximate, not a verified Schwab market-data contract |
| `exchanges` | - | `DEFAULT_EXCHANGES` | Schwab exchange name or code -> `XASE` / `XNYS` / `ARCX` / `XNAS` |
| `minute_max_lookback_days` / `minute_chunk_days` | - | 48 / 10 (at most 10) | |

Only access tokens (`SCHWAB_ACCESS_TOKEN` and `SCHWAB_ACCESS_TOKEN_<ID>`) reach the VPS. The secrets agent writes the token and its `.expires_at` file into the US runtime-secret volume. App credentials and the refresh token stay in Cloudflare.

## Data

- `source` is `schwab`, or `schwab:shadow` while shadow. Alias kind is `schwab_symbol`.
- `source_channel` is `<instance>:instruments` or `<instance>:pricehistory`.
- Bindings in [bindings.yaml](../../configs/ingestion/bindings.yaml):
  - `{dataset: ref.listings, market: USA, provider: schwab, role: shadow, priority: 20}  # -> secondary`
  - `{dataset: market.bars, market: USA, resolution: daily, provider: schwab, role: primary, priority: 10}`
  - `{dataset: market.bars, market: USA, resolution: 1min, provider: schwab, role: shadow, priority: 10}   # -> primary`
- Listings: exactly one exact-symbol match with `assetType` EQUITY or ETF and a mapped exchange is required. Anything else yields no record. The CUSIP becomes the US ISIN (`isin_from_cusip`), so aliases attach to EODHD listings with `high` confidence.
- Bars: daily is one unit per window. 1min windows are clamped to the last 48 days and split into 10-day chunks. Requests send `needExtendedHoursData=false`.

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-ingest` and `factorlab-calendars` (XNYS `bounds`, `NY`). Third-party: `pydantic`, `requests`.
- R1/R8 ([test_boundaries.py](../../tests/architecture/test_boundaries.py)): no storage, no other provider, no `pandas`, `clickhouse_connect`, `sqlalchemy` or `fastapi`.
- `normalize` is pure over the capture and uses `fetched_at` for session cutoffs. A 1min bar is kept only inside the XNYS regular session and once its minute has finished. A daily bar is kept only once its session has closed. A repeated timestamp keeps the last candle.
- `Set-Cookie` headers are dropped from captures, and `source_url` carries the sorted query parameters.
- Account pool ([transport.py](src/factorlab/sources/schwab/transport.py)):
  - Each request goes to the ready account with the most free budget; ties rotate.
  - A 429 benches the account for `Retry-After` (default 60 s). A 401 on an unrotated
    token benches it until the token changes, for at most 5 minutes.
  - After a 429 or 401, the request moves on to another account, trying each account
    at most once.
  - With no usable account the transport raises `AuthRequired`, or `RateLimited` when
    every account is throttled, without making an HTTP call.
  - `metadata.schwab_account` records the account that served the request.
  - Setup: [05 §Pooled Schwab accounts](../../docs/architecture/05-secrets-and-upstox-auth.md#pooled-schwab-accounts).

## Observability

- Logger `factorlab.sources.schwab.normalize` warns on dropped unaligned, invalid or inconsistent candles. Unlike legacy, a bad candle does not fail the symbol.
- Engine pipeline names are `market.bars.daily:schwab`, `market.bars.1min:schwab` and `ref.listings:schwab`. The engine writes `meta.ingestion_runs` and `raw.archive`. `AuthRequired` skips the instance's remaining units.
- The `ingest-us` health check is `running`. Use the `factorlab-logs` and `factorlab-clickhouse` skills.

## Tests

`uv run pytest providers/schwab`

- [test_conformance.py](tests/test_conformance.py) runs the shared kit over `tests/fixtures/market.bars/` (daily, minute) and `tests/fixtures/ref.listings/` (`instrument_brk_b`, `instrument_unknown`).
- Regenerate expectations after an intentional change: `uv run python -m factorlab.testkit.conformance regen schwab`.

## Release and rollback

- A provider is never released alone. It ships in the `ingest-us` image: `providers: [schwab, eodhd, github_csv, edgar]` in [component.yaml](../../components/ingest-us/component.yaml) and in [providers.py](../../components/ingest-us/src/factorlab/components/ingest_us/providers.py). Rollback class is `writer`.
- The maintained `ingest-us daemon` uses [collector.py](../../components/ingest-us/src/factorlab/components/ingest_us/collector.py) and this package through the shared engine. Installed image revisions determine whether production has adopted the cutover.
- Bindings changes are config, not code. `configs/` is baked into the image, so a bindings change ships with the next `ingest-us` image. Cutover follows 07 §15.2.

## Pitfalls

- Shadow traffic shares the production tokens and their approximate request budgets with the
  collector and universe validator. Limit simultaneous traffic explicitly; separate
  processes do not coordinate pacing. Run shadow off-hours or on a small `--listing` set.
- Schwab prices are split-adjusted, while EODHD `close` is raw. The two disagree on any day before a split. Decide which is authoritative before `bars_best` (07 §15.1).
- The `.expires_at` companion has a dotted name. In practice it is readable only as a file (`FACTORLAB_SECRETS_DIR/SCHWAB_ACCESS_TOKEN.expires_at`), not as a plain env var.
- Pre-1970 daily candles are handled with epoch arithmetic, because `datetime.fromtimestamp` rejects negative values on Windows. Keep it that way.

**Indexes and shares (F-042, [ADR-0017](../../docs/decisions/0017-sector-index-methodology.md)):**

- **Index values.** Quotes and instruments with asset type `INDEX` (`$SPX`, `$COMPX`,
  `$NDX`, `$DJI`, `$RUT`, `$VIX`) normalize to `product_type = 'index'` on exchange
  `INDX` (`INDEX_VENUE`; Schwab exchange name `Index`). Their daily bars use the normal
  `/pricehistory` path.
- **ETFs on the quotes path.** These stay `common`, because `product_type` is part of the
  `market.bars` key.
- **Shares outstanding.** `SchwabSharesOutstanding` (`fundamentals.shares_outstanding`,
  `/quotes?fields=fundamental` in batches of 50) reads `fundamental.sharesOutstanding`,
  dated to the capture's New York day. It is not registered with the engine until a
  recorded production capture becomes its fixture. `universe-us` runs it through an
  explicit binding (`EngineClient.collect_shares`).

**US futures (F-043, [feature](../../docs/features/F-043-us-futures-quotes.md)):**

- **Quotes only.** `/pricehistory` serves no futures, so everything comes from
  `/marketdata/v1/quotes`, in batches of up to 50. Nothing here calls `/trader/v1`.
  Pure helpers live in [futures.py](src/factorlab/sources/schwab/futures.py).
- **Symbols.** A root is `/ES`; a dated contract is `/ESZ26` (root, CME month code,
  two-digit year). Roster entries (`FuturesRoot`) carry root, MIC (`FUTURES_VENUES`),
  product type, listing cycle and depth. MICs double as `ref.exchanges` codes.
- **Roots.** `SchwabListings` with `params.mode: futures_roots` emits one
  `ref.listings` record per quoted root.
- **Contracts.** `SchwabFuturesContracts` (`ref.contracts`) quotes each root together
  with its next `depth + 2` cycle contracts. It keeps the root's active contract (the
  quote whose `reference.product` is the root and `futureIsActive` is true; Schwab keys
  it by the contract symbol, not the root) and the next ones by
  `futureExpirationDate`, up to `depth`. `params.as_of` supplies
  the date, so `plan` stays pure.
- **Snapshots.** `SchwabFuturesQuotes` (`market.futures_quotes`) maps
  `params.contracts` (symbol -> MIC) to `ContractQuoteRecord`s. `observed_at` is the
  vendor `quoteTime`, falling back to `tradeTime` and then `fetched_at`. Settlement is
  read from `quote.settlementPrice`, then `reference.futureSettlementPrice`.
- **Missing roots.** A root Schwab does not quote is logged and skipped, which is the
  case for `/VX` and `/DX` (`invalidSymbols`; not in the roster). A price of `0.0` is
  read as missing. A quotes capture with none of its
  contracts raises `NormalizationError`.
- **Not registered.** Both futures sources stay unregistered until a recorded
  production capture becomes their fixture. `ingest-us futures` runs them through
  explicit bindings (`EngineClient.collect_futures_roster`, `collect_futures_quotes`).
  Field names are confirmed against production responses (2026-10-21); the unit
  payloads in `tests/test_futures.py` follow that shape. Recording a conformance fixture
  is still pending.
