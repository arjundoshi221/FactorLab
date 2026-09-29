# factorlab-provider-eodhd

> EODHD adapter for the US: the listed-equity master, S&P 500 index components, and unadjusted daily bars (per-symbol history or daily bulk).

## Purpose

This is a provider member. It implements `plan -> fetch -> normalize` ([07 §6](../../docs/architecture/07-ingestion-provider-abstraction.md)) for `ref.listings`, `ref.universe_membership` and `market.bars` (daily) in market `USA`.
Importing `factorlab.sources.eodhd` registers `EodhdListings`, `EodhdUniverse` and `EodhdDailyBars` ([`__init__.py`](src/factorlab/sources/eodhd/__init__.py)).
All bindings are `shadow`. Production universe and listing work still runs through the legacy `ingest-us` modules.

## Owns and does not own

- Owns: API-key transport with redacted capture URLs and quota handling ([client.py](src/factorlab/sources/eodhd/client.py)), planning ([sources.py](src/factorlab/sources/eodhd/sources.py)), and pure normalization of `CODE.US` symbols, venues, `Type` strings and date-only bars ([normalize.py](src/factorlab/sources/eodhd/normalize.py)).
- Does not own: identity minting or the 20%-drop snapshot guard for universes (`factorlab-ingest` `universe_snapshot.py` plus the storage sinks), ClickHouse writes, or the production universe job (`components/ingest-us` legacy `eodhd/` and `universe/`).

## Entry points

- Registered sources: `EodhdListings` (`/exchange-symbol-list/US`), `EodhdUniverse` (`/fundamentals/GSPC.INDX?filter=Components`) and `EodhdDailyBars` (`/eod/{CODE}.US` or `/eod-bulk-last-day/US?date=`).
- Run it through the component: `factorlab-ingest-us engine run --dataset market.bars --market USA --resolution daily --instance eodhd --universe sp500 --dry-run`.

## Configuration and secrets

[settings.py](src/factorlab/sources/eodhd/settings.py) loads [configs/sources/eodhd.yaml](../../configs/sources/eodhd.yaml).

| Setting | Env / secret name | Default | Notes |
|---|---|---|---|
| `api.key_env` | `EODHD_API_KEY` | same | Sent as query parameter `api.key_param` (`api_token`) and only on the wire |
| `api.base_url` / `api.timeout` | - | `https://eodhd.com/api` / 60s | |
| `rate_limits.requests_per_second` / `requests_per_minute` | - | 16.0 / 1000 | Sliding windows of 1s and 60s |
| `default_exchange` | - | `US` | Used for the listings and bulk endpoints |
| `venues`, `listing_types` | - | `DEFAULT_VENUES`, `{"common stock": "common"}` | Venue name -> `XNAS` / `XNYS` / `XASE` / `ARCX` |
| `bulk_max_days` | - | 5 (1 to 31) | Bulk mode plans at most this many trailing session days |
| `universes.<code>` | - | none in code. The YAML sets `sp500: {symbol: GSPC.INDX, minimum_constituents: 450}` | `EodhdIndex` is `extra="forbid"` |

`EODHD_API_KEY` is listed under `secrets` in [ingest-us component.yaml](../../components/ingest-us/component.yaml).

## Data

- `source` is `eodhd`, or `eodhd:shadow` while shadow. Alias kind is `eodhd_symbol` (`CODE.US`).
- `source_channel` is `<instance>:exchange_symbol_list`, `<instance>:index_components`, `<instance>:eod` or `<instance>:eod_bulk`.
- Bindings in [bindings.yaml](../../configs/ingestion/bindings.yaml):
  - `{dataset: ref.listings, market: USA, provider: eodhd, role: shadow, priority: 10}   # -> primary`
  - `market.bars` USA `daily`, `role: shadow`, `priority: 20`, `params: {mode: bulk}` (target secondary)
  - `{dataset: ref.universe_membership, market: USA, provider: eodhd, role: shadow, priority: 20}       # -> secondary (cross-check)`
- Bars use EODHD's raw `close`, which is unadjusted as 06 requires. `adjusted_close` stays only in `raw.archive`. Bars are stamped at New York midnight, and a day is emitted only once its XNYS session had closed by `fetched_at`.
- Listings keep USD rows with a mapped venue and type. A ticker listed twice with different details is skipped and logged.

## Dependencies and contracts

- Workspace: `factorlab-core`, `factorlab-ingest` and `factorlab-calendars` (`NY`, `is_session`, `bounds`). Third-party: `pydantic`, `requests`.
- R1/R8 ([test_boundaries.py](../../tests/architecture/test_boundaries.py)): only core, calendars and ingest. No storage, no other provider, no dataframe, DB or web stack.
- HTTP 402 is `QuotaExhausted` (a subclass of `AuthRequired`), so it ends the instance's run. Other statuses go through `raise_for_status`.
- The universe `minimum_constituents` check runs inside `normalize` from capture metadata, so a replay applies the same check.

## Observability

- Logger `factorlab.sources.eodhd.normalize` warns on inconsistent bars and on conflicting duplicate tickers.
- Engine pipelines are `ref.listings:eodhd`, `ref.universe_membership:eodhd` and `market.bars.daily:eodhd`. The engine writes `meta.ingestion_runs`, `raw.archive` and `meta.unresolved_entities`. Read them with the `factorlab-logs` and `factorlab-clickhouse` skills.

## Tests

`uv run pytest providers/eodhd`

- [test_conformance.py](tests/test_conformance.py) runs the shared kit against `tests/fixtures/market.bars/` (`eod_history`, `eod_bulk_day`), `ref.listings/` (`us_symbol_list`) and `ref.universe_membership/` (`sp500_components`).
- Regenerate expectations after an intentional change: `uv run python -m factorlab.testkit.conformance regen eodhd`.

## Release and rollback

- A provider is never released alone. It ships inside the `ingest-us` image ([component.yaml](../../components/ingest-us/component.yaml), [providers.py](../../components/ingest-us/src/factorlab/components/ingest_us/providers.py)). Rollback class is `writer`.
- Production is still the legacy path. `ingest-us universe --config /app/config/us-universe.yaml --daemon` runs [legacy/universe_daemon.py](../../components/ingest-us/src/factorlab/components/ingest_us/legacy/universe_daemon.py) with `legacy/eodhd/` and `legacy/universe/`.
- Bindings changes are config, not code. `configs/` is baked into the image, so they ship with an `ingest-us` image. Cutover follows 07 §15.2.

## Pitfalls

- The API key must never reach `raw.archive`. It is added only to the outgoing request. `source_url` and metadata carry the redacted `safe` parameters, and `Set-Cookie` is dropped. Keep it that way when you add endpoints.
- EODHD bills calls unevenly. Per `eodhd.yaml`, a bulk call costs 100 and a fundamentals call (the universe fetch) costs 10. The paid daily limit is 100000 and resets at 00:00 UTC.
- Bulk mode puts every requested instrument in one unit (`max_batch=100_000`) and skips non-session days.
- EODHD (raw) and Schwab (split-adjusted) disagree before splits. See 07 §15.1 before promoting a `bars_best` pair.
- Adding a key under `universes.<code>` in the YAML fails settings validation (`extra="forbid"`).
