# factorlab-provider-upstox

> Upstox adapter for India (NSE): the instrument master for listings and stock-futures contracts, plus 1min bars from candles or batch quotes.

## Purpose

This is a provider member. It implements the engine's `plan -> fetch -> normalize` contract
([07 §6](../../docs/architecture/07-ingestion-provider-abstraction.md)) for four datasets in market `IND`.
Importing `factorlab.sources.upstox` registers the sources ([`__init__.py`](src/factorlab/sources/upstox/__init__.py)).
Every binding is still `shadow`. Production still runs the legacy daemon (see Release and rollback).

## Owns and does not own

- Owns: Upstox transport, auth and rate limiting ([client.py](src/factorlab/sources/upstox/client.py)), unit planning ([sources.py](src/factorlab/sources/upstox/sources.py)), and the pure mapping from Upstox bytes to records ([normalize.py](src/factorlab/sources/upstox/normalize.py)). Segment filters, IST handling and candle/quote quirks stop here.
- Does not own: ClickHouse writes, identity minting and `*_best` reads (`factorlab-storage` sinks). It also does not own the run loop, retries or the archive (`factorlab-ingest` engine), token refresh (the Cloudflare secrets agent, [05](../../docs/architecture/05-secrets-and-upstox-auth.md)), or the production daemon (`components/ingest-india` legacy).

## Entry points

- Registered sources: `UpstoxListings` (`ref.listings`), `UpstoxContracts` (`ref.contracts`), `UpstoxBars` (`market.bars`) and `UpstoxContractBars` (`market.futures_contract_bars`).
- `historical_windows()` in [sources.py](src/factorlab/sources/upstox/sources.py) splits a date range into inclusive IST-date chunks.
- Run it through the component: `factorlab-ingest-india engine run --dataset market.bars --market IND --resolution 1min --dry-run ...`. `engine` is [orchestration's CLI](../../libs/orchestration/src/factorlab/orchestration/cli.py).

## Configuration and secrets

[settings.py](src/factorlab/sources/upstox/settings.py) loads [configs/sources/upstox.yaml](../../configs/sources/upstox.yaml). An `instances.<name>` block in that file overrides fields for a second instance.

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `api.token_env` | `UPSTOX_ACCESS_TOKEN` | same | Bearer token. [`get_secret`](../../libs/core/src/factorlab/core/secrets.py) re-reads it before every request. |
| `api.base_url` / `api.timeout` | - | `https://api.upstox.com` / 20s | |
| `rate_limits.per_second / per_minute / per_30_minutes` | - | 50 / 500 / 2000 | `SlidingWindowLimiter` windows of 1s, 60s and 1800s |
| `instruments_url` | - | `assets.upstox.com/.../{exchange}.json.gz` | Public. Fetched without auth. |
| `exchanges`, `listing_instrument_types`, `contract_underlying_types` | - | `NSE`, `EQ`, `EQUITY` | Index futures and options are skipped |
| `history_available_from`, `historical_chunk_days` | - | 2022-01-01, 30 (at most 31) | |
| `quote_batch_size` | - | 100 (at most 100) | |
| `retries` | - | 2 | Declared here, but the engine applies its own `DEFAULT_RETRIES = 2` |

The token expires daily. The YAML notes `token_expiry: "03:30 IST"`.

## Data

- `source` is `upstox`. A shadow binding writes `upstox:shadow`. Alias kind is `upstox_instrument_key` (`ALIAS_KIND`).
- `source_channel` is `<instance>:instruments`, `<instance>:v3_historical`, `<instance>:v3_intraday` or `<instance>:v3_quote_ohlc`.
- Bindings in [bindings.yaml](../../configs/ingestion/bindings.yaml), all `role: shadow`, `priority: 10`:
  - `{dataset: ref.listings, market: IND, provider: upstox, role: shadow, priority: 10}`
  - `{dataset: ref.contracts, market: IND, provider: upstox, role: shadow, priority: 10}`
  - `market.bars` IND `1min`, `params: {universe: full_nse_eq, mode: quote}`
  - `market.futures_contract_bars` IND `1min`, `params: {mode: candles}`
- Bar modes. `candles` (the default) makes one unit per instrument per V3 historical window for past IST days, plus one V3 intraday unit for today. `quote` makes one V3 OHLC-quote unit per 100 instruments.
- Target tables: `market.bars` and `market.futures_contract_bars`, where shadow rows are really written. `ref.listings` and `ref.contracts` get no writes while shadow.

## Dependencies and contracts

- Workspace: `factorlab-core` (secrets) and `factorlab-ingest` (datasets, errors, `RawCapture`, `SlidingWindowLimiter`, `raise_for_status`). Third-party: `pydantic`, `requests`, `tzdata`.
- R1/R8 in [test_boundaries.py](../../tests/architecture/test_boundaries.py): import only `factorlab.core`, `factorlab.calendars` and `factorlab.ingest`. Never import storage, another provider, `pandas`, `clickhouse_connect`, `sqlalchemy` or `fastapi`.
- `normalize` is pure over the capture bytes and capture metadata: no clock, network or secrets. The instrument refs travel in `capture.metadata["instruments"]`, so `raw.archive` rows replay exactly.
- HTTP statuses map to the taxonomy through `raise_for_status`: 401/403 -> `AuthRequired`, 429 -> `RateLimited`, 5xx -> `TransientError`, other non-200 -> `PermanentError`. On a 401, if the token changed mid-flight, the request is retried once with the new token.

## Observability

- Logger names are `factorlab.sources.upstox.*`. Dropped inconsistent candles and malformed master rows are logged as warnings.
- The engine logs under pipeline `<dataset>[.<resolution>]:<instance>` (for example `market.bars.1min:upstox`). It writes `meta.ingestion_runs`, `raw.archive` and parks unresolved rows in `meta.unresolved_entities`.
- `AuthRequired` fails the current unit and skips the rest of that instance's run. The component's health check is `running` ([component.yaml](../../components/ingest-india/component.yaml)). Read logs and run rows with the `factorlab-logs` and `factorlab-clickhouse` skills.

## Tests

`uv run pytest providers/upstox`

- [test_conformance.py](tests/test_conformance.py) runs the shared kit ([source_conformance.py](../../libs/testkit/src/factorlab/testkit/source_conformance.py)) against `tests/fixtures/<dataset>/*.capture.json`, `*.expected.json` and `requests.json`.
- [test_upstox_provider.py](tests/test_upstox_provider.py) covers the error taxonomy, token rotation, planning and an end-to-end run into `FakeClickHouse` sinks. It imports storage and testkit, so run `uv sync --all-packages` first.
- After an intentional normalize change: `uv run python -m factorlab.testkit.conformance regen upstox`, then review the diff.

## Release and rollback

- A provider is never released alone. It ships inside the `ingest-india` image, which lists `providers: [upstox]` in [component.yaml](../../components/ingest-india/component.yaml) and `PROVIDERS = ("upstox",)` in [providers.py](../../components/ingest-india/src/factorlab/components/ingest_india/providers.py). Rollback class is `writer`.
- Production today is `ingest-india daemon --universe full_nse_eq --daemon`, which runs the legacy [daemon.py](../../components/ingest-india/src/factorlab/components/ingest_india/legacy/daemon.py) over `legacy/upstox/`. This package is not in that path.
- Bindings changes are config, not provider code. `configs/` is copied into the image (`COPY configs /app/configs`), so a bindings change still reaches production only through an `ingest-india` image release.
- Cutover follows 07 §15.2: one full NSE session of shadow parity (`upstox` vs `upstox:shadow`), then promote to `primary` and swap the compose command.

## Pitfalls

- `tick_size` is stored as Upstox reports it, in paise (RELIANCE ₹0.10 -> `10.0`). That matches legacy parity. Changing it is a deliberate data change.
- Quote-mode bars have `oi=None`. A `live_ohlc` minute that is still forming (`ts >=` the fetch minute) is skipped. Candle endpoints pass `oi` through.
- Candle arrays arrive newest-first with IST offsets. `normalize` sorts them, converts to UTC and keeps the last duplicate of a repeated minute.
- Contract expiry is the UTC date of the epoch-millisecond value, the same as legacy storage.
- Non-200 responses are never archived, because `fetch` raises before a capture exists.
- A shadow run must never write plain `source='upstox'`. That would replace the legacy rows, since `source` is part of the sort key.
