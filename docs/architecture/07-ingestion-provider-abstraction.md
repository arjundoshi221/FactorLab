# Ingestion Provider Abstraction

> Status: `[beta]` — every phase P1–P8 is built and tested; every provider is bound as `shadow`. Production daemons still run the legacy paths until each cutover (§15.2). Decision record in [developments/011](../developments/011-provider-abstraction.md).
> Last verified: 2026-09-24

This document is the contract between three layers:

- the **DB service**, which writes canonical ClickHouse v2 data;
- a **provider-agnostic ingestor layer**;
- the **provider adapters** (Upstox, Schwab, EODHD, IBKR, House Clerk, EDGAR, …).

The goal: switching, adding or duplicating a provider changes that provider's package and a line of config, and nothing else.

[02](02-database-clickhouse.md) is *why* ClickHouse. [06](06-schema-rehau.md) is *what* the tables are. This doc is *how* data gets from a vendor into those tables.

---

## 1. Purpose and scope

**Problem.** Today each provider decides how its data reaches the database. Specifically:

- Storage classes carry provider knowledge:
  - `source="upstox"` / `source="schwab"` defaults throughout `storage/v2_india.py` and `storage/v2_us.py`;
  - Upstox segment filtering (`NSE_EQ`/`EQ`, `NSE_FO`/`FUT`) inside `V2IndiaStorage.sync_instruments` / `sync_contracts`;
  - a Schwab-specific listing key (`schwab:USA:{symbol}`) in `V2USStorage.reference()`.
- Daemons call `start_ingestion_run` / `finish_ingestion_run` by hand and pass pandas frames or vendor-shaped dicts into storage.
- Only IBKR (`sources/ibkr/provider.py`) uses the typed contract in `shared/ingest/provider.py`, and even IBKR's storage Protocol (`BrokerStorage`) belongs to the provider rather than to the dataset.
- `configs/sources/*.yaml` is not read by any code.

As a result, you cannot replace Upstox, add a second US bars vendor, or run a shadow provider without editing storage and orchestration.

**Scope.**

- **In scope:**
  - every writer into the ClickHouse v2 namespaces (`ref`, `market`, `fundamentals`, `alt`, `broker`, `meta`, `raw`);
  - the code that selects and schedules providers.
- **Out of scope:**
  - Legacy Postgres ingestion (`storage/ingest.py`, `countries/us/political/*/ingest.py`, the Postgres-writing Upstox and Schwab scripts). It is frozen and retired phase by phase (§15).
  - Streaming or websocket transports (§16).
  - The *read* API, beyond the priority view (§10).

---

## 2. Terminology

| Term | Meaning | Example |
|---|---|---|
| **Provider** | An external vendor or source system. Its name is written into the `source` column. | `upstox`, `schwab`, `eodhd`, `ibkr`, `house_clerk`, `edgar` |
| **Instance** | One configured copy of a provider, with its own credentials, rate budget and `source_channel` prefix. | `upstox`, `upstox_backup` |
| **Dataset** | A canonical data product, identified by its primary v2 table. | `market.bars`, `ref.listings` |
| **Record** | The canonical, provider-neutral row shape for a dataset. | `BarRecord` |
| **Source adapter** | A provider's implementation of one dataset: `plan → fetch → normalize`. | `UpstoxIntradayBars` |
| **Sink** | The DB-service writer for one dataset. It is the only code that touches its tables. | `ClickHouseBarSink` |
| **Engine** | The generic runner that drives a source adapter into a sink inside an ingestion run. | `shared/ingest/engine.py` |
| **Binding** | A config entry: dataset × market × (resolution) × provider instance × role × priority. | "IND 1min bars ← upstox, primary" |
| **Unit** | One fetch-and-write step, which becomes one outcome in `RunContext` (success or failure). | one symbol window, one 100-key quote batch |

---

## 3. Layer model and dependency rules

```text
┌───────────────────────────────────────────────────────────────────────────┐
│ Orchestration   scripts/factlab_*.py daemons · cron · CLI                 │
│                 reads configs/ingestion/bindings.yaml                     │
└───────────────┬───────────────────────────────────────────────────────────┘
                │ registry.source_for(binding)   ← never imports a provider
┌───────────────▼───────────────────────────────────────────────────────────┐
│ Engine          shared/ingest/engine.py (provider-agnostic)               │
│                 ingestion_run → plan → fetch → archive → normalize → write│
└───────┬──────────────────────────────────────────────┬────────────────────┘
        │ DatasetSource protocol                       │ Sink / port protocols
┌───────▼───────────────────────┐        ┌─────────────▼──────────────────────┐
│ Provider adapters             │        │ Dataset contracts (shared)         │
│ sources/<provider>/           │ uses → │ shared/ingest/datasets/*           │
│ auth · transport · rate limit │        │ records · requests · Sink protocols│
│ vendor → canonical normalize  │        └─────────────▲──────────────────────┘
└───────────────────────────────┘                      │ implements
                                         ┌─────────────┴──────────────────────┐
                                         │ DB service  storage/sinks/*        │
                                         │ identity resolution · UUID minting │
                                         │ provenance · version · ClickHouse  │
                                         └────────────────────────────────────┘
```

### 3.1 Hard rules

| # | Rule | Enforced by |
|---|---|---|
| R1 | `factorlab.sources.*` never imports `factorlab.storage.*`. | §14 import scan |
| R2 | `factorlab.storage.*` never imports `factorlab.sources.*` or `factorlab.countries.*`. | §14 import scan |
| R3 | `factorlab/storage/` contains no provider name as a string literal. The only exception is the alias-kind registry described in §8.4. | §14 literal scan |
| R4 | The engine, the orchestration scripts and `shared/ingest/*` never import a concrete provider module. They obtain sources only from the registry. | §14 import scan |
| R5 | Only the DB service mints canonical UUIDs (`storage/canonical_ids.py`). Records carry references (§5.2), never `listing_id` / `security_id` / `entity_id`. | Record types have no ID fields |
| R6 | Every fact row is written with a `Provenance` from an open run, and `raw_id` points to the capture it was normalized from. | Sink lineage check (§7.2) |
| R7 | `normalize` is pure. It reads only the `RawCapture`, never a live client or the clock. | §13 conformance (network disabled) |

### 3.2 Package layout

```text
src/factorlab/shared/ingest/
  provider.py              existing: RawCapture, Provenance, RunContext, ingestion_run, run_provider
  errors.py                NEW: provider error taxonomy (§6.3)
  registry.py              NEW: register_source / source_for (§9.1)
  bindings.py              NEW: pydantic bindings + provider settings loader (§9.2)
  engine.py                NEW: DatasetProvider adapter → run_provider (§11)
  memory.py                NEW: InMemorySink(s) on NullProviderStorage for dry runs and tests
  ratelimit.py             NEW: SlidingWindowLimiter configured from provider settings
  datasets/
    __init__.py            DATASETS catalogue (DatasetSpec per dataset id)
    common.py              InstrumentRef, EntityRef, Capabilities, FetchUnit, DatasetSource
    ports.py               ReferenceReader, CheckpointStore (read-side DB ports, §8.3, §11.3)
    reference.py           InstrumentRecord, ContractRecord + sinks, ReferenceMode
    universe.py            ConstituentRecord, UniverseSink, UniverseReader
    market.py              BarRecord, ContractBarRecord, BarRequest + sinks
    political.py           Legislator/Committee/Membership/Filing/Trade records + sinks
    broker.py              PositionSnapshot, AccountStateRow, ExecutionRecord, OpenOrderSnapshot + sink
    fundamentals.py        FilingRecord, LineItemRecord + sink
src/factorlab/storage/sinks/     ClickHouse v2 implementations of every Sink/port
src/factorlab/sources/<provider>/
  __init__.py              register_source(...) calls only
  settings.py              typed settings model for configs/sources/<provider>.yaml
  client.py                session, auth, rate limiter
  <dataset>.py             DatasetSource classes
  normalize.py             pure vendor → record functions
configs/ingestion/bindings.yaml
configs/sources/<provider>.yaml
```

---

## 4. Dataset catalogue

Each dataset has a stable id, one record type, one sink protocol and a fixed set of target tables. A provider can implement any subset of datasets.

| Dataset id | Target v2 tables (06 §) | Record | Sink protocol | Providers today → after migration |
|---|---|---|---|---|
| `ref.listings` | `ref.entities`, `ref.securities`, `ref.listings`, `ref.identifier_aliases` (§3) | `InstrumentRecord` | `InstrumentSink` | Upstox, EODHD, Schwab |
| `ref.contracts` | `ref.contracts`, `ref.identifier_aliases` (§3) | `ContractRecord` | `ContractSink` | Upstox |
| `ref.universe_membership` | `ref.universes`, `ref.universe_membership` (§3) | `ConstituentRecord` | `UniverseSink` | GitHub CSV, EODHD index components (built); NSE archives (later) |
| `market.bars` | `market.bars` (§4) | `BarRecord` | `BarSink` | Upstox (1min), Schwab (1min, daily), EODHD (daily) |
| `market.futures_contract_bars` | `market.futures_contract_bars` (§4) | `ContractBarRecord` | `ContractBarSink` | Upstox |
| `ref.legislators` | `ref.entities`, `ref.identifier_aliases`, `ref.legislator_terms`, `alt.political_committees`, `alt.political_committee_memberships` | `LegislatorRecord`, `CommitteeRecord`, `MembershipRecord` | `LegislatorSink` | congress-legislators |
| `alt.political_filings` | `alt.political_filings` (§6) | `PoliticalFilingRecord` | `PoliticalFilingSink` | House Clerk (annual index) |
| `alt.political_trades` | `alt.political_trades`, `alt.political_filings.trade_count` (§6) | `PoliticalTradeRecord` | `PoliticalTradeSink` | House Clerk (PTR PDFs); request from `PoliticalReader.recent_filings` |
| `broker.snapshot` | `broker.positions_snapshot`, `broker.account_state_snapshot`, `broker.executions`, `broker.open_orders_snapshot` (§11) | `datasets/broker.py` (moved from `sources/ibkr/shapes.py`, which re-exports) | `BrokerSink` | IBKR |
| `fundamentals.filings` | `fundamentals.filings`, `fundamentals.line_items` (§5) | `FundamentalFilingRecord`, `LineItemRecord` | `FundamentalsSink` | EDGAR company facts (new, P8) |

Adding a row to this table is the **only** change that requires DB-service work (§12.4). `market.quotes` and `market.options_bars` are candidates for v2 (§16).

`raw.archive` and `meta.*` are not datasets. They are written by the engine and the sinks as part of every run: `meta.ingestion_runs`, `meta.unresolved_entities`, `meta.recovery_state`, `meta.source_status` and `meta.session_coverage`.

---

## 5. Canonical records

### 5.1 Rules for every record

1. Records are `@dataclass(frozen=True, slots=True)` in `shared/ingest/datasets/`, following the style of `sources/ibkr/shapes.py`. No pandas objects, no dicts and no vendor objects cross the boundary.
2. Every `datetime` is timezone-aware UTC and is validated in `__post_init__` (the same check as `_require_utc` in `provider.py`). Session-local dates are `date`.
3. Prices, amounts and quantities are `Decimal`. Volumes and counts are `int`. Missing values are `None`, never `0` or a sentinel.
4. Enumerated values use the vocabulary from [06](06-schema-rehau.md): `resolution` ∈ `'1min','5min','15min','1h','daily',…`; `product_type` ∈ `'common','etf','index','single_stock_future',…`; `session` ∈ `'regular','pre','post',…`.
5. Vendor quirks are removed inside `normalize`. Examples:
   - epoch-millisecond expiries;
   - IBKR "unset" sentinels (`to_decimal` in `sources/ibkr/normalize.py`);
   - Upstox segment codes;
   - Schwab candle arrays.

   A sink that has to understand a vendor format is a bug.
6. Records never carry canonical UUIDs (R5). They carry references.
7. Fields that have no canonical column yet go in `attributes: Mapping[str, str]`. Sinks ignore `attributes`; the value is still preserved in `raw.archive`. Promoting an attribute to a field is a dataset change (§12.4).

### 5.2 References

```python
@dataclass(frozen=True, slots=True)
class InstrumentRef:
    alias_kind: str            # vendor alias kind, e.g. 'upstox_instrument_key', 'eodhd_symbol'
    alias_value: str           # e.g. 'NSE_EQ|INE002A01018', 'AAPL.US'
    exchange_code: str         # ref.exchanges code, e.g. 'NSE', 'XNAS'
    trading_symbol: str        # exchange ticker as listed
    country_code: str          # ISO-2
    isin: str | None = None
    cusip: str | None = None
    figi: str | None = None

@dataclass(frozen=True, slots=True)
class EntityRef:               # people and organisations (legislators, committees, issuers)
    alias_kind: str            # 'bioguide', 'cik', ...
    alias_value: str
    entity_type: str           # 'person_legislator', 'committee', 'issuer', ...
    name: str | None = None
```

A reference is enough for the DB service to resolve or mint identity (§8), and it is provider-neutral. Two providers describing the same NSE listing produce two `InstrumentRef`s that share `exchange_code`, `trading_symbol` and `isin` but differ in `alias_kind` and `alias_value`.

### 5.3 Market records

`BarRecord` corresponds to `market.bars` (06 §4).

| Field | Type | Notes |
|---|---|---|
| `instrument` | `InstrumentRef` | resolved to `listing_id` / `security_id` / `entity_id` by the sink |
| `resolution` | `str` | 06 vocabulary |
| `bar_time` | `datetime` UTC | bar **open** time. For daily/weekly/monthly, any instant whose exchange-local date is the trade date; the sink keys it at **exchange-local midnight** (what production US daily rows use; 06 §13.2's `00:00 UTC` is superseded, see §15.1) |
| `open`, `high`, `low`, `close` | `Decimal \| None` | not adjusted |
| `volume` | `int \| None` | shares or contracts |
| `turnover` | `Decimal \| None` | listing-currency notional |
| `trades_count` | `int \| None` | |
| `oi` | `int \| None` | derivatives only |
| `settlement_price` | `Decimal \| None` | derivatives only |
| `session` | `str \| None` | `None` means the sink derives it from `ref.sessions`. The calendar lives in the DB service, not in the provider; this is today's IST tagging in `V2IndiaStorage`. |

`trade_date`, `product_type`, `country_code` and the provenance columns are filled in by the sink.

`ContractBarRecord` has the same bar fields, with `contract: InstrumentRef` in place of `instrument`, and maps to `market.futures_contract_bars`.

`BarRequest(market, resolution, instruments: Sequence[InstrumentRef], start, end)` is built by the engine (§11.3). A provider never decides which instruments to fetch.

### 5.4 Reference records

| Record | Key fields |
|---|---|
| `InstrumentRecord` | `ref: InstrumentRef`, `name`, `product_type`, `currency`, `lot_size`, `tick_size`, `first_traded`, `last_traded`, `sector_raw`, `attributes` |
| `ContractRecord` | `ref: InstrumentRef` (the contract's own vendor alias), `underlying: InstrumentRef`, `product_type`, `expiry: date`, `right: 'C' \| 'P' \| None`, `strike: Decimal \| None`, `lot_size`, `tick_size`, `weekly: bool` |
| `ConstituentRecord` | `universe_code`, `instrument: InstrumentRef` (often a bare ticker: no alias, blank exchange), `universe_name`, `weight: Decimal \| None`. A source returns the **complete** membership of each universe it covers; the sink derives `effective_from`/`effective_to` by diffing snapshots (§15.1) |

The provider decides which of its instruments belong to a dataset. Example: the Upstox adapter keeps `NSE_EQ`/`EQ` for `ref.listings` and `NSE_FO`/`FUT` for `ref.contracts`. The filter moves **out** of `V2IndiaStorage` and into `sources/upstox/`.

### 5.5 Political, broker and fundamentals records

- **Political.** `LegislatorRecord`, `CommitteeRecord`, `MembershipRecord`, `PoliticalFilingRecord`, `PoliticalTradeRecord`. They carry:
  - raw fields as filed (`ticker_raw`, `legislator_name_raw`, amount-range text);
  - `EntityRef` / `InstrumentRef` hints when the provider has them;
  - a `native_key`: a source-native id, or the content-hash inputs that `sources/political/house_clerk.py` uses today.

  The sink mints `political_trade_id(native_key)` and runs the name and ticker resolvers. Field tables are fixed in P5 against 06 §6.
- **Broker.** The existing frozen dataclasses in `sources/ibkr/shapes.py` (`PositionSnapshot`, `AccountStateRow`, `ExecutionRecord`, `OpenOrderSnapshot`) are already provider-neutral: they carry `broker_code` and `vendor_id`. They move to `datasets/broker.py` unchanged in P6.
- **Fundamentals.** `FilingRecord` and `LineItemRecord`, fixed in P8 against 06 §5.

---

## 6. Source adapter contract

### 6.1 Protocol

This formalises the loop that `IBKRBrokerProvider._snapshot_mode` already runs.

```python
@dataclass(frozen=True, slots=True)
class FetchUnit:
    name: str                       # run-unit name, e.g. 'RELIANCE:2026-09-24' or 'quote-batch:17'
    params: Mapping[str, Any]       # provider-private; opaque to the engine
    source_channel: str             # '<instance>:<endpoint>', e.g. 'upstox:v3_intraday'

@dataclass(frozen=True, slots=True)
class Capabilities:
    markets: frozenset[str]                 # {'IND'}
    resolutions: frozenset[str] = frozenset()   # {'1min'}; empty for non-bar datasets
    alias_kind: str = ""                    # outbound identity (§8.3), e.g. 'upstox_instrument_key'
    max_lookback: timedelta | None = None
    max_batch: int = 1                      # instruments per unit
    polling: bool = False                   # supports near-real-time polling

class DatasetSource(Protocol[RequestT, RecordT]):
    provider: ClassVar[str]                 # value written to `source`
    dataset: ClassVar[str]                  # catalogue id (§4)
    capabilities: ClassVar[Capabilities]

    def plan(self, request: RequestT) -> Sequence[FetchUnit]: ...
    def fetch(self, unit: FetchUnit) -> RawCapture: ...
    def normalize(self, capture: RawCapture) -> Sequence[RecordT]: ...
```

- **`plan`** is pure. It splits a request into units that fit the vendor's limits. Examples:
  - `historical_windows` in `countries/in_/equities/upstox/candles.py` becomes Upstox's `plan`;
  - batches of 100 keys for the v3 batch quote.
- **`fetch`** is impure. It does transport, auth, retries and rate limiting, and returns exactly one `RawCapture` holding the unmodified response bytes. If an endpoint needs several HTTP calls for one unit, `fetch` returns a single capture whose body is an envelope. This follows the pattern of `sources/ibkr/capture.py`: one decodable payload per unit.
- **`normalize`** is pure (R7). The engine calls it only on the captured bytes, never on live objects. That is what makes `raw.archive` replayable (§11.4).

A source that emits two record types (political filings and trades, broker's four datasets) declares `RecordT` as a union. Its sink accepts that union.

### 6.2 Construction, auth and settings

- A source is constructed by the registry as `SourceCls(settings: <Provider>Settings, instance: str)`. Tests can inject the transport, as IBKR's `connector` and `clock` do today.
- `<Provider>Settings` is a pydantic model loaded from `configs/sources/<provider>.yaml`. It holds base URLs, rate limits, timeouts and secret *names*, never secret values.
- Secrets come only from `core/secrets.get_secret()`, following the Cloudflare-managed model in [05](05-secrets-and-upstox-auth.md). An instance's secrets are namespaced by prefix: instance `upstox_backup` reads `UPSTOX_BACKUP_ACCESS_TOKEN`.
- Rate limiters are per instance and owned by the adapter (`UpstoxRateLimiter`, Schwab's 1 req/s). The engine runs one instance's units sequentially unless the settings declare `concurrency > 1`.

### 6.3 Error taxonomy (`shared/ingest/errors.py`)

| Error | Meaning | Engine behaviour |
|---|---|---|
| `AuthRequired` | token missing or expired (Upstox 03:30 IST, Schwab 7-day refresh) | fail the current unit **and short-circuit** the remaining units for that instance; set `meta.source_status` to `reauth_required` |
| `RateLimited(retry_after)` | vendor throttle | sleep for `retry_after` (bounded), retry the unit once, then fail it |
| `TransientError` | network error, 5xx, timeout | retry with backoff up to `settings.retries`, then fail the unit |
| `PermanentError` | 4xx, unknown symbol, schema change | fail the unit, no retry |
| `NormalizationError` | captured bytes cannot be parsed | fail the unit; the capture is **already archived**, so it can be replayed after a fix |

Mapping today's exceptions: `sources/schwab/market.py:AuthRequired` → `AuthRequired`; `shared/ingest/http.py:RateLimitDeferred` → `RateLimited`; `sources/ibkr/errors.py:IBKRCaptureError` → `NormalizationError`. Adapters subclass or raise the shared types. Any other exception fails the unit, as it does in `RunContext.fail_unit` today.

---

## 7. Sink contract (DB service)

### 7.1 Protocols

Each sink extends the existing `ProviderStorage` (raw archive + ingestion runs) and adds exactly one write method, which takes records:

```python
class BarSink(ProviderStorage, Protocol):
    def write_bars(self, rows: Sequence[BarRecord], *, provenance: Provenance) -> WriteResult: ...

class InstrumentSink(ProviderStorage, Protocol):
    def upsert_instruments(self, rows: Sequence[InstrumentRecord], *, provenance: Provenance,
                           mode: ReferenceMode = "authoritative") -> WriteResult: ...

@dataclass(frozen=True, slots=True)
class WriteResult:
    rows_written: int
    unresolved: int          # rows parked in meta.unresolved_entities (§8)
    resolved: int            # identities found (the only signal in resolve_only mode)
```

The same pattern gives `ContractSink.upsert_contracts`, `ContractBarSink.write_contract_bars`, `UniverseSink.write_constituents`, `LegislatorSink.write_legislators`, `PoliticalTradeSink.write_political`, `BrokerSink.write_snapshot` and `FundamentalsSink.write_fundamentals`.

### 7.2 Guarantees every sink gives

1. **Lineage.** Writes require an open run, and `provenance.ingest_run_id` must equal the active run. This generalises `V2BrokerStorage._check_lineage` and `V2IndiaStorage`'s "start an ingestion run before writing v2 bars" guard. `source`, `source_channel`, `raw_id`, `as_of_time` and `ingested_at` come from `Provenance.columns()`.
2. **Idempotency.** Re-writing the same records is safe. The ReplacingMergeTree natural key plus a monotonic `version` (`storage/clickhouse.py:_version`) makes the latest write win (06 §1, 02 "Idempotency").
3. **Identity.** Resolved per §8. Rows that cannot be resolved are parked, not dropped.
4. **Enrichment.** Denormalised columns (`security_id`, `entity_id`, `product_type`, `trade_date`, `session`) are filled from `ref.*`.
5. **Provider blindness.** A sink never branches on `provenance.source`, and has no default `source=` parameter (R3). Two providers writing the same dataset use the identical code path.

### 7.3 Implementation

The implementations in `storage/sinks/` are thin adapters over the existing v2 writers:

- `V2IndiaStorage.write_candles_1min_batch`;
- `V2USStorage.write_daily_records`;
- `V2ReferenceWriter.upsert_listing` / `upsert_future`;
- `V2PoliticalClickHouseStorage.write_*`;
- `V2BrokerStorage.write_*`.

As each provider migrates, the provider-specific code inside those writers is removed: defaults, segment filters, key formats and DataFrame handling. The old methods are deleted once no caller remains. `InMemorySink` (`shared/ingest/memory.py`) implements every protocol for dry runs and tests.

---

## 8. Identity resolution

### 8.1 Inbound resolution (records → canonical IDs)

For each `InstrumentRef`, the sink tries these steps in order:

1. **Alias.** Look up `ref.identifier_aliases` by `(alias_kind, alias_value)` where `valid_to` is null. If a row exists, use its target.
2. **Natural key.** Look for an existing listing by `(isin, exchange_code)`, falling back to `(exchange_code, trading_symbol)` for active listings. When the reference has no exchange (a bare ticker from an index list), the scope is `country_code` instead, and a match is accepted only if exactly one active listing qualifies. If found, attach the new alias to it (`confidence` from the step taken) and use it.
3. **Mint.** Only in a reference dataset (`ref.listings`, `ref.contracts`), and only for a `primary` binding. The engine passes `mode=binding.reference_mode`:
   - `primary` → `authoritative`: resolve, mint when new, write attributes and alias;
   - `secondary` → `alias_only`: attach the provider's alias to an identity that already exists (`V2ReferenceWriter.attach_alias`); never mint, never overwrite attributes;
   - `shadow` → `resolve_only`: resolve and report `resolved`/`unresolved`; write nothing to `ref.*`.

   In authoritative mode the sink mints IDs from the **natural key** (`storage/canonical_ids.py:natural_listing_ids`, `natural_contract_id`), then writes the entity, security, listing and alias.
4. **Park.** In a fact dataset (`market.*`, `alt.*`, `broker.*`), an unresolved instrument goes to `meta.unresolved_entities` (06 §13.9). The fact row is either dropped or written with null identity, according to that dataset's policy in 06. Fact datasets never mint.

This is what makes providers interchangeable. A second India bars vendor supplies `isin=INE002A01018, exchange_code=NSE`, resolves at step 2 onto the listing that Upstox created, and its bars land on the same `listing_id` with a different `source`.

### 8.2 Existing IDs are frozen

Today's IDs are derived from provider keys:

- India: `listing_id = uuid5(LISTING_NAMESPACE, "factorlab:ref_instruments:{upstox instrument_key}")` in `storage/canonical_ids.py`.
- US: `USA:{symbol}`, and separately `schwab:USA:{symbol}`.

They are **never re-keyed**. Existing rows stay reachable through their aliases (step 1) and natural keys (step 2). Step 3 uses a new natural-key derivation for new listings only (§16, open question Q2).

The Schwab duplicate-key bug (`V2USStorage.reference()` at `storage/v2_us.py:78` uses `schwab:USA:{symbol}`, while `sync_reference_master` and `upsert_resolved_constituents` use `USA:{symbol}`) is fixed by step 2. The P4 cleanup merges any duplicate listings it has already produced, through `ref.identifier_aliases`.

### 8.3 Outbound mapping (canonical → provider identifiers)

Providers do not choose the universe. The engine does:

1. The engine reads the canonical `listing_id`s to fetch: members of a universe (`UniverseReader.universe_members`, e.g. `factlab_ingest.py run … --universe sp500`), or an explicit list.
2. It asks `ReferenceReader.aliases_for(listing_ids, alias_kind=source.capabilities.alias_kind)`.
3. It builds a `BarRequest` whose `InstrumentRef`s carry the provider's own alias.
4. Listings with no alias for that provider are recorded as a failed unit (`unmapped:<alias_kind>`) and in `meta.unresolved_entities`. They are never silently skipped.

This generalises `V2BrokerStorage.resolve_vendor_ids`, which is read-only alias resolution for `ibkr_conid`.

### 8.4 Alias kinds

Alias kinds are data, not code paths. The v1 list: `upstox_instrument_key`, `eodhd_symbol`, `schwab_symbol`, `ibkr_conid`, `bioguide`, `cik`, `ticker`.

Adding a provider adds a kind. The kind string is declared in the provider's `Capabilities` and appears in storage only as an opaque value. The single permitted provider-shaped literal in storage is a validation set, `KNOWN_ALIAS_KINDS`, which replaces `BROKER_ALIAS_KIND` in `v2_broker.py`.

---

## 9. Registry and bindings

### 9.1 Registry (`shared/ingest/registry.py`)

```python
register_source(SourceCls)                       # called from sources/<p>/__init__.py
source_for(binding: Binding) -> DatasetSource    # builds settings, instantiates, validates capabilities
registered() -> Mapping[tuple[str, str], type]   # (provider, dataset) -> class; used by conformance tests
```

- Discovery is an explicit tuple of provider package names in `sources/__init__.py`, which is a new file (today `sources/` is a namespace package). There is no entry-point or filesystem magic, so a provider that is not listed cannot load by accident.
- Registering the same `(provider, dataset)` twice is an error at import time.

### 9.2 Bindings (`configs/ingestion/bindings.yaml`)

```yaml
version: 1
bindings:
  - dataset: ref.listings
    market: IND
    provider: upstox
    role: primary
  - dataset: market.bars
    market: IND
    resolution: 1min
    provider: upstox
    instance: upstox            # default = provider name
    role: primary               # primary | secondary | shadow | disabled
    priority: 10                # lower wins in market.bars_best (§10)
    params: {universe: full_nse_eq}
  - {dataset: market.bars, market: USA, resolution: daily, provider: schwab, role: primary,   priority: 10}
  - {dataset: market.bars, market: USA, resolution: daily, provider: eodhd,  role: secondary, priority: 20}
  - {dataset: broker.snapshot, market: USA, provider: ibkr, role: primary, params: {modes: [paper, live]}}
```

When the file is loaded (pydantic, `bindings.py`), these checks run:

- the dataset exists in `DATASETS`;
- `(provider, dataset)` is registered;
- `market` and `resolution` are within `capabilities`;
- `instance` names are unique;
- two `primary` bindings for the same dataset × market × resolution are an error.

The load fails fast at daemon start.

**Roles.** `source` is in every fact table's sort key, so a shadow binding writing `source='upstox'` would *replace* the legacy Upstox rows rather than sit beside them. Shadow runs therefore write archive, run and fact rows under `Binding.source_name` = `<provider>:shadow`; parity compares `source='upstox'` against `source='upstox:shadow'`. On promotion the binding writes plain `source='upstox'`.


| Role | Writes? | Read by `*_best`? | Use |
|---|---|---|---|
| `primary` | yes | yes, by priority | the normal source |
| `secondary` | yes | yes, as a fallback where primary has no row | redundancy, gap fill |
| `shadow` | only where `source` is in the table's key (`market.bars`, `market.futures_contract_bars`; `DatasetSpec.source_keyed`), under `source = '<provider>:shadow'`. Elsewhere (`ref.*`, `broker.*`, `alt.*`, `fundamentals.*`) it fetches, archives and normalizes but writes nothing. | **no** | trial a provider against the incumbent (§13.3) without touching the incumbent's rows |
| `disabled` | no | no | keep config but stop |

### 9.3 Provider settings (`configs/sources/<provider>.yaml`)

These files exist today as documentation. They become live, loaded into `sources/<p>/settings.py`. A per-instance override block lets duplicated instances differ:

```yaml
# configs/sources/upstox.yaml — existing keys (api, rate_limits, endpoints, data_limits) kept as-is
name: upstox
api:
  base_url: https://api.upstox.com
  token_env: UPSTOX_ACCESS_TOKEN         # a secret NAME, resolved through get_secret()
rate_limits: {per_second: 50, per_minute: 500, per_30_minutes: 2000}
instances:                               # NEW: per-instance overrides
  upstox_backup:
    api: {token_env: UPSTOX_BACKUP_ACCESS_TOKEN}
    rate_limits: {per_second: 25}
```

The stale `vendor_key: upstox  # matches ref.security_aliases.vendor` line is replaced by the provider's `alias_kind` in `Capabilities` (§8.4).

---

## 10. Multi-source storage and priority reads

- **Write side.** Every `primary`, `secondary` and `shadow` binding writes its own rows. `source` is already in the `market.bars` ORDER BY key `(country_code, product_type, listing_id, resolution, source, bar_time)`, so vendors never collide (06 §13.1). `source_channel` carries the instance.
- **Read side.** 06 §13.1 designs `market.bars_best` with a hard-coded `arrayIndexOf(['ibkr','eodhd','schwab','upstox'], source)`. Both it and `ref.source_priorities` are listed in `sql/clickhouse/v2/deferred.json`. P7 promotes them in a new migration wave. The steps:
  1. Specify concrete `ref.source_priorities(dataset, country_code, resolution, source, priority, role, valid_from)` DDL in 06 and regenerate with `scripts/generate_clickhouse_v2_schema.py`. v2 DDL is never hand-edited.
  2. Rewrite `market.bars_best` to join on `ref.source_priorities` instead of the literal array, and exclude `role = 'shadow'`.
  3. The engine syncs the bindings' `priority` and `role` into `ref.source_priorities` at daemon start (an idempotent upsert), so config is the single source of truth.
- **Consumers.** Research and API reads use `*_best` by default. Querying a specific `source` stays possible for vendor reconciliation.

---

## 11. Runtime

### 11.1 Engine

`engine.py` adapts `(binding, source, sink)` into the existing `Provider` Protocol, so `run_provider` / `ingestion_run` (`shared/ingest/provider.py`) are reused unchanged:

```python
class DatasetProvider:                 # satisfies shared.ingest.provider.Provider
    source: ClassVar[str]              # set per instance from the binding
    def collect(self, ctx: RunContext) -> None:
        request = self._build_request()                 # §11.3 — canonical universe → provider aliases
        for unit in self._source.plan(request):
            try:
                capture = self._fetch_with_policy(unit)  # §6.3 retries / short-circuit
                raw_id = ctx.archive(capture, source_channel=unit.source_channel)
                rows = self._source.normalize(capture)
                result = self._write(rows, ctx.provenance(
                    source_channel=unit.source_channel, raw_id=raw_id,
                    as_of_time=capture.fetched_at))
            except Exception as exc:
                ctx.fail_unit(unit.name, exc); continue
            ctx.succeed_unit(unit.name, result.rows_written)
```

This is the same unit isolation as `IBKRBrokerProvider._snapshot_mode`. A run ends `success`, `partial` or `failed`, and is recorded in `meta.ingestion_runs`.

### 11.2 Orchestration

Daemons stay as long-running services (`deploy/compose.production.yml`), but each becomes generic:

```text
factlab_ingest.py --bindings configs/ingestion/bindings.yaml --dataset market.bars --market IND --daemon
```

The daemon loads the bindings, asks the registry for each enabled binding's source, and loops on its schedule. The existing helpers in `shared/runtime/` (`supervised`, `acquire_lock`, `Heartbeat`, `GracefulShutdown`) remain the loop scaffolding.

During migration, the current scripts (`factlab_india_clickhouse_5min.py`, `factlab_us_clickhouse.py`, `factlab_political_bootstrap.py`, `us_portfolio_ibkr_snapshot.py`) are rewritten as thin wrappers over the engine. The compose service names do not change.

### 11.3 Incremental state

Gap and recovery state stays provider-neutral. `CheckpointStore` (a DB port backed by `meta.recovery_state` and `meta.session_coverage`, generalising `V2USStorage.state` / `gap_start` and `ClickHouseStorage.latest_candle_times`) is keyed by `(dataset, instance, listing_id, resolution)`.

The engine computes `start`/`end` for each instrument from the checkpoint and `capabilities.max_lookback`, then hands the request to `plan`. So switching providers resumes from the canonical watermark, not from a vendor-specific cursor.

### 11.4 Dry run and replay

- **Dry run:** `--dry-run` swaps in `InMemorySink`, built on `NullProviderStorage`, as `DryRunBrokerStorage` does today.
- **Replay:** `factlab_ingest.py replay --raw-id <uuid>` loads captures (as `ArchivedCapture`, which keeps the original `source` and `source_channel`) from `raw.archive`, runs the registered source's `normalize` and writes through the sink in a new run whose `metadata` says `replay_of`. This works because of R7. It is also how a normalization bug fix is backfilled without re-fetching.

---

## 12. Recipes

Each recipe lists **every** file that changes. If a change needs more than this, the abstraction has leaked: fix the leak, not the recipe.

### 12.1 Add a provider for an existing dataset

1. `src/factorlab/sources/<p>/`: `__init__.py` (register), `settings.py`, `client.py`, `<dataset>.py`, `normalize.py`.
2. `configs/sources/<p>.yaml`.
3. `src/factorlab/sources/__init__.py`: add `<p>` to the provider tuple.
4. `tests/fixtures/providers/<p>/<dataset>/*`: recorded captures, plus the expected records.
5. `configs/ingestion/bindings.yaml`: a new binding with `role: shadow`.
6. Secrets registered in the Cloudflare Secrets Store (05) under the provider's prefix.
7. If the provider brings a new alias kind, add it to `KNOWN_ALIAS_KINDS` (§8.4).

### 12.2 Switch provider

1. `configs/ingestion/bindings.yaml`: promote the new binding to `primary` and demote the old one to `secondary` or `disabled`.
2. Nothing else. History from the old provider stays under its `source`, and `*_best` reads switch at once through the synced priorities.

### 12.3 Duplicate a provider (second account, key or region)

1. `configs/sources/<p>.yaml`: add an `instances.<name>` override (secret names, rate limits).
2. `configs/ingestion/bindings.yaml`: add a binding with `instance: <name>`.
3. Add the new secrets to the Secrets Store.

Rows get `source=<p>` and `source_channel=<name>:<endpoint>`. Two non-shadow instances of one provider write the same keys, so the later write wins: that is intended for a backup key or account, which should return identical data.

### 12.4 Add a dataset (the only DB-service change)

1. Record types, request type and sink protocol in `shared/ingest/datasets/<area>.py`, and a `DatasetSpec` in `DATASETS`.
2. DDL in 06, then regenerate and migrate through `scripts/generate_clickhouse_v2_schema.py` / `scripts/migrate_clickhouse_v2.py`.
3. The ClickHouse sink in `storage/sinks/`, plus `InMemorySink` support.
4. A row in the §4 catalogue, and sink conformance tests.
5. At least one provider, per §12.1.

---

## 13. Testing and conformance

### 13.1 Source conformance (`tests/contracts/test_source_conformance.py`)

This file is parametrised over `registry.registered()`. Each registered `(provider, dataset)` must have fixtures in `tests/fixtures/providers/<p>/<dataset>/`: `*.capture.json` (a serialised `RawCapture`) and `*.expected.json`. The suite asserts:

- `normalize(capture)` equals the expected records, and is deterministic (called twice).
- `normalize` runs with the network disabled (socket patched) and without `get_secret`.
- Every record passes its dataset validators: UTC datetimes, `Decimal` prices, `low ≤ open, close ≤ high`, a non-empty `alias_kind` / `alias_value`, and a resolution in capabilities.
- `plan` never produces a unit larger than `max_batch` or longer than `max_lookback`.
- The `source_channel` of every unit starts with the instance name.

A provider **cannot be registered without passing this suite.**

### 13.2 Sink conformance (`tests/contracts/test_sink_conformance.py`)

This suite runs every sink protocol against two implementations: `InMemorySink`, and the ClickHouse sink over the recording `FakeClient` pattern from `tests/clickhouse_v2/test_v2_india.py`. It asserts:

- the lineage guard (§7.2.1);
- idempotency (a second write gives a higher `version` and the same keys);
- the order of identity resolution (§8.1);
- the park-don't-drop behaviour for unresolved rows;
- the same rows from two providers giving the same `listing_id` with different `source`.

The engine is tested with fake sources and `InMemorySink`: unit isolation, the error policy (§6.3) and the `AuthRequired` short-circuit.

### 13.3 Shadow parity (operational)

Before a binding is promoted from `shadow`, a parity check compares one full session of its rows against the incumbent source. The comparison covers row counts per listing, OHLC within tolerance, and missing or extra bars. The report is attached to the migration PR. This is how P3 and P4 prove the new path is equivalent to the old one.

---

## 14. Enforcement

`tests/architecture/test_boundaries.py` uses only stdlib `ast`, so it adds no new dependency.

1. **Import scan.** Parse every module under `src/factorlab/` and scripts under `scripts/`, then assert R1, R2 and R4.
2. **Literal scan.** Assert that no string literal under `src/factorlab/storage/` equals a registered provider name (R3), with `KNOWN_ALIAS_KINDS` as the only exemption.
3. **Allowlist.** `tests/architecture/boundary_allowlist.txt` lists today's violations (for example the `source="upstox"` defaults in `storage/v2_india.py`). The test fails if a violation appears that is not listed, **and** if a listed violation no longer exists, so the list can only shrink. Every migration phase removes its entries. P8's exit criterion is an empty file.

---

## 15. Migration phases

Storage and schema changes wait until the ClickHouse v2 table cutover is done (gated for 2026-09-25, `docs/operations/clickhouse-v2-data-completion.md`). Each phase ships on its own and leaves production running.

| Phase | Work | Exit criterion |
|---|---|---|
| **P0 Spec** ✅ | This doc; [ADR 011](../developments/011-provider-abstraction.md); index updates | Accepted |
| **P1 Foundation** ✅ | `datasets/`, `registry.py`, `bindings.py`, `engine.py`, `errors.py`, `memory.py`; conformance kits; boundary test with the full allowlist. `provider.py` unchanged. | All existing tests green; the new suites pass against fake sources |
| **P2 DB-service sinks** ✅ | `storage/sinks/*` over the v2 writers; natural-key identity resolver (§8); `ReferenceReader` and `CheckpointStore` ports | Sink conformance green; storage allowlist entries shrink |
| **P3 Upstox** 🟡 adapter built and bound as shadow; cutover pending | `countries/in_/equities/upstox/*` → `sources/upstox/` (instruments, contracts, 1min bars including the batch-quote path, futures bars); segment filters move out of `V2IndiaStorage`; `factlab_india_clickhouse_5min.py` and `…_premarket.py` wrap the engine | One NSE session in shadow parity with the old path, then cut over; the old Upstox Postgres scripts deleted |
| **P4 US** 🟡 Schwab, EODHD and GitHub CSV (universe) adapters built and bound as shadow; cutover pending | `sources/schwab/` (bars, reference), new `sources/eodhd/` (reference master, daily bars, universe), GitHub CSV universe as a source; fix the listing-key bug and merge duplicates; `factlab_us_clickhouse.py` and `factlab_us_universe.py` wrap the engine | Parity on US `market.bars` daily and 1min; legacy `countries/us/equities/*` deleted |
| **P5 Political** 🟡 built, shadow | `sources/political/house_clerk.py` and `references.py` behind `ref.legislators` / `alt.political_trades`; `factlab_political_bootstrap.py` wraps the engine | Parity on `alt.political_trades`; `countries/us/political/*` marked deprecated |
| **P6 IBKR** 🟡 built, shadow | `BrokerStorage` → `BrokerSink`; shapes move to `datasets/broker.py` | IBKR tests green; no behaviour change |
| **P7 Multi-source** 🟡 built; wave 10 not yet applied | `ref.source_priorities` + `market.bars_best` wave (§10); bindings → priorities sync | EODHD `secondary` alongside Schwab `primary`; `*_best` resolves by config |
| **P8 Proof + retirement** 🟡 EDGAR built; retirement awaiting confirmation (§15.3) | EDGAR fundamentals as a brand-new provider and dataset through §12.4; delete `storage/ingest.py`, the legacy political ingest modules and the remaining Postgres scripts (keep `migrations/` for history); refresh `ingestion-inventory.md` | Boundary allowlist empty; the EDGAR change touched only the files §12.4 lists |

---

## 15.1 Implementation notes (P1–P8, 2026-09-24)

What exists:

| Piece | Where |
|---|---|
| Records, requests, sink protocols, catalogue | `src/factorlab/shared/ingest/datasets/` (`common.py`, `market.py`, `reference.py`, `ports.py`, `__init__.py`) |
| Registry, bindings, engine, errors, limiter, in-memory sink | `src/factorlab/shared/ingest/{registry,bindings,engine,errors,ratelimit,memory}.py` |
| ClickHouse sinks, identity resolver, market calendar | `src/factorlab/storage/sinks/{clickhouse,identity,calendar}.py` |
| Upstox adapter | `src/factorlab/sources/upstox/{settings,client,normalize,sources}.py` |
| EODHD adapter | `src/factorlab/sources/eodhd/{settings,client,normalize,sources}.py` (listings; daily bars in `history` and `bulk` modes) |
| Schwab adapter | `src/factorlab/sources/schwab/{settings,transport,normalize,sources}.py` (per-symbol listings; daily + 1min bars). `client.py` / `market.py` are the untouched legacy path |
| GitHub CSV adapter | `src/factorlab/sources/github_csv/` (`ref.universe_membership` from index CSVs; config `configs/sources/github_csv.yaml`) |
| Universe snapshots | `shared/ingest/datasets/universe.py`, `shared/ingest/universe_snapshot.py`; `EodhdUniverse` in `sources/eodhd/sources.py` |
| Shared helpers | `shared/ingest/transport.py` (HTTP status → taxonomy), `shared/ingest/identifiers.py` (CUSIP → ISIN), `errors.QuotaExhausted` |
| Bindings | `configs/ingestion/bindings.yaml` (all IND and USA bindings are `shadow`, with target roles noted) |
| Runner | `scripts/factlab_ingest.py validate / run [--dry-run] / replay` |
| Tests | `tests/contracts/` (source + sink conformance, fake ClickHouse), `tests/architecture/test_boundaries.py` + `boundary_allowlist.txt`, `tests/shared/test_ingest_{engine,bindings,datasets}.py`, `tests/sources/upstox/`, `tests/sources/us/`, `tests/test_factlab_ingest_cli.py` |

Decisions, and known differences from the legacy paths to check during the shadow parity runs:

- **Shadow isolation** (found while building P4): shadow bindings write under `<provider>:shadow` and never write `ref.*` (§9.2). Without this a shadow run would have overwritten the incumbent's rows.
- **Period bars are keyed at exchange-local midnight.** Production US daily rows have always used New York midnight (`V2USStorage.write_daily`), not 06 §13.2's `00:00 UTC`. The sink canonicalises, so Schwab (`05:00Z` stamps) and EODHD (date-only) land on one key per day. 06 §13.2 should be corrected to match.
- **Schwab prices are split-adjusted; EODHD `close` is raw.** 06 wants unadjusted prices in `market.bars`. Production already stores Schwab's adjusted values, so parity holds, but a Schwab-primary / EODHD-secondary pair will disagree on any day before a split. Decide before P7 (`bars_best`) which one is authoritative, or fetch Schwab unadjusted if the API allows.
- **Schwab resolves by CUSIP.** `/instruments` returns a CUSIP; `identifiers.isin_from_cusip` turns it into the US ISIN, so Schwab aliases attach to EODHD's listings with `high` confidence instead of symbol matching.
- **The `schwab:USA:{symbol}` duplicate-listing bug** (`V2USStorage.reference()`) cannot recur on the new path: nothing mints from vendor keys. Existing duplicates still need a one-off merge. Where a symbol is duplicated, the natural-key steps see two candidates and **park** instead of guessing. To list them:

  ```sql
  SELECT exchange_code, trading_symbol, groupArray(listing_id) AS listing_ids, count() AS n
  FROM ref.listings FINAL
  WHERE country_code = 'US' AND active
  GROUP BY exchange_code, trading_symbol
  HAVING n > 1
  ORDER BY n DESC, trading_symbol;
  ```
- **The EODHD API key never reaches `raw.archive`.** It is added to the request only; `source_url` and metadata carry the redacted parameters. `Set-Cookie` headers are dropped from captures. HTTP 402 (daily quota) is `QuotaExhausted`, which ends the instance's run.
- **Invalid Schwab candles are dropped, not fatal.** The legacy normalizer failed the whole symbol.
- **Shadows never overwrite** (found in P5/P6): `broker.*`, `alt.political_*` and `fundamentals.*` have no `source` in their sort keys, so `DatasetSpec.source_keyed` is false and the engine runs their shadow bindings count-only (`DatasetProvider.counts_only`). Only `market.bars` and `market.futures_contract_bars` get real `<provider>:shadow` rows.
- **Political (P5).** `sources/congress_legislators` and `sources/house_clerk` (parsers moved to `house_clerk/parse.py`; the legacy fetchers re-export them and no longer import storage, removing two R1 entries). Legislators get a `legislator_name` alias (`identifiers.legislator_name_key`, the legacy rule), so filers are resolved by the DB service; a name shared by two legislators stays unresolved instead of being guessed. Re-syncing the filing index keeps `trade_count` (the legacy writer reset it to 0). Tickers are canonicalised (`BRK.B` -> `BRK-B`) before point-in-time resolution; the legacy path never matched share classes. The PTR parser is unchanged, including its known quirk of reading `BILL` from "US Treasury Bill [GS]" (06 §15 Wave 4 Tier 1 item 2 tracks the fix).
- **IBKR (P6).** `IbkrBrokerSnapshot` reproduces `IBKRBrokerProvider` record for record (`tests/sources/ibkr/test_snapshot_source.py`), holds one connection per Gateway per run and fails a dead Gateway's four units after one attempt. Its source channels are `ibkr:<mode>_gateway`; the legacy job writes `<mode>_gateway`, so switching changes that column's values in `broker.*`.
- **Multi-source (P7).** `ref.source_priorities` is in 06 §13.1 and generated into forward wave 10 (`FORWARD_TABLES` in the generator keeps waves 1–8 byte-identical); `market.bars_best` is a read-time view (Q1). `factlab_ingest.py sync-priorities` writes priorities from bindings; readable bindings of one key must have distinct priorities.
- **EDGAR (P8).** `EdgarCompanyFacts` + `fundamentals.filings` were added with only the §12.4 files: a dataset module, a catalogue entry, a sink (`storage/sinks/fundamentals.py`), `canonical_ids.filing_id`, the source, a binding and fixtures. Issuers resolve by `cik` alias; failing that, by the ticker hint's listing, after which the `cik` alias is recorded (`medium`). Company facts carry only filing dates, so `filed_at`/`accepted_at` are midnight UTC of that date; `statement` is `unclassified`.
- **Universe membership is a dataset now** (Q4). `sources/github_csv` (CSV of tickers) and `EodhdUniverse` (index components) return complete snapshots. The sink (`shared/ingest/universe_snapshot.py`, used by both sinks):
  - opens a membership on the snapshot date for new members, and closes leavers with `effective_to` = the day before;
  - refuses a snapshot that resolves to nothing or drops more than 20% of current members (`SnapshotRejected`), the counterpart of production's 80%-of-previous master guard;
  - resolves bare tickers within the country, parks what it cannot resolve, and writes `ref.universes` on first use.

  Constituent minimums (`minimum_constituents`) are checked in `normalize` from the capture metadata, so a replay applies the same check. Production Schwab-validation is replaced by resolution against listings the reference providers own; an S&P name missing from `ref.listings` is parked, not published. `universe/factory.py` and `factlab_us_universe.py` keep running production until cutover.

- **Only primary bindings mint identity** (§8.1 step 3). Shadow reference bindings resolve and alias only.
- **Invalid OHLC is dropped in `normalize`**, for both endpoints. The legacy candle path wrote inconsistent candles; the legacy quote path already dropped them. The bytes stay in `raw.archive`.
- **Quote-path `oi` is `None`**, not `0`. The quote endpoint does not report open interest; the candle endpoints pass through Upstox's value.
- **`tick_size` is stored as Upstox reports it, which is paise** (RELIANCE ₹0.10 → `10.0`). That matches today's `V2IndiaStorage`, so parity holds, but the stored value is not in rupees. Correcting it is a deliberate data change, made in `normalize`.
- **Non-200 responses are not archived.** Fetch raises before a capture exists; the legacy client archived error bodies.
- **`V2ReferenceWriter` gained optional `ids`/`confidence` (listings) and `canonical_id`/`alias_kind` (futures).** The defaults keep the legacy behaviour, so today's callers are unchanged. Alias `confidence` records how identity was found: `exact` (alias), `high` (ISIN + exchange), `medium` (symbol).
- **Index futures and options are skipped** by default (`contract_underlying_types: [EQUITY]`), matching `V2IndiaStorage.sync_contracts`. The ClickHouse contract sink parks options until a writer exists.

---

## 15.2 Cutover runbook (per provider, after the ClickHouse v2 table swap)

Each cutover is a bindings change plus a Compose command change; no code changes.

1. Run the shadow for one full session: `factlab_ingest.py daemon --dataset … --market … [--resolution …] --sessions-only` with the binding at `role: shadow`.
2. Parity: compare `source = '<provider>'` (legacy) with `source = '<provider>:shadow'` for that session (row counts per listing, OHLC, missing bars). For count-only datasets compare the run summaries' normalized counts with the legacy run's rows.
3. Promote the binding to `primary` (and the displaced provider to `secondary` or `disabled`), then `factlab_ingest.py sync-priorities`.
4. Replace the legacy service command in `deploy/compose.production.yml`:

| Service | Legacy command | Engine command |
|---|---|---|
| `ingest-india` | `factlab_india_clickhouse_5min.py --universe full_nse_eq --daemon` | `factlab_ingest.py daemon --dataset market.bars --market IND --resolution 1min --exchange NSE --interval-seconds 60 --sessions-only` (`--exchange NSE` = every active NSE listing, today's `full_nse_eq`), plus a daily `run --dataset ref.listings` / `ref.contracts` before the open |
| `ingest-us` | `factlab_us_clickhouse.py --universe us_listed_equities --daemon` | `factlab_ingest.py daemon --dataset market.bars --market USA --resolution daily --universe sp500 --interval-seconds 3600` and a `1min` daemon with `--sessions-only` |
| `universe-us` | `factlab_us_universe.py --config … --daemon` | `factlab_ingest.py daemon --dataset ref.universe_membership --market USA --interval-seconds 86400` |
| `ingest-political` | `factlab_political_bootstrap.py --ptr-limit 20` | `run --dataset ref.legislators`, `run --dataset alt.political_filings`, `run --dataset alt.political_trades --recent-filings 20` |
| `ibkr-snapshot` | `us_portfolio_ibkr_snapshot.py --daemon --run-on-start` | `factlab_ingest.py run --dataset broker.snapshot --market USA` at the 06:00 / 16:30 ET slots |

5. After a week on the engine, delete the legacy module(s) for that provider (§15.3) and shrink `tests/architecture/boundary_allowlist.txt`.

## 15.3 Legacy retirement (awaiting owner confirmation)

Static import closure of every production entrypoint (Compose services, cron, the API) shows these tracked files are **not reachable from production** and are superseded by ClickHouse paths. They are *not* deleted yet because `docs/operations/windows-task-scheduler.md` lists them as hand-registered Task Scheduler jobs (its 2026-06-16 state says none were scheduled); confirm nothing still runs them, then delete in one commit:

- `src/factorlab/storage/ingest.py` (Postgres upserts)
- `scripts/in/equities/upstox/india_equities_upstox_{historical,live}.py`
- `scripts/us/equities/schwab/us_equities_schwab_{eod,historical,live,auth}.py` and `src/factorlab/countries/us/equities/schwab/`
- `scripts/us/equities/eodhd/us_equities_eodhd_daily.py`

Kept on purpose: `storage/db.py`, `storage/schemas/*` and `countries/us/political/*`, which carry the legacy-only political sources (FEC, LDA, Senate eFD, Senate Stock Watcher, Congress.gov, USAspending, Finnhub) that have no ClickHouse port yet (§16). The production-used legacy paths (`countries/in_/equities/upstox`, `sources/schwab/market.py`, `countries/us/equities/eodhd/{client,instruments,us_universe,configured_universe}`, `universe/*`, the `storage/v2_*` writers) go after their cutovers (§15.2 step 5).

## 16. Not doing, and open questions

### Not doing

- **Plugin auto-discovery** (entry points, filesystem scanning). Providers are listed explicitly (§9.1).
- **Streaming and websocket abstraction.** Upstox 1min polling and the Schwab REST tier fit the `plan → fetch` model. Schwab streaming ([developments/002](../developments/002-live-us-market-data.md)) will get its own contract when it lands.
- **Porting the legacy-only political sources** (FEC, LDA, Senate eFD, Senate Stock Watcher, Congress.gov, USAspending, Finnhub contracts). They are deprecated where they stand. Each gets a port through §12.1 when research needs it.
- **Data-quality-aware vendor selection.** Priority is static config (06 §17, F2). Quality-aware selection belongs downstream.
- **Changing existing canonical IDs** (§8.2).

### Open questions

- **Q1.** *Resolved (2026-09-24, 06 rev 12):* read-time view over `ref.source_priorities`; readers filter by listing and time range.
- **Q2.** How should new natural-key listing IDs be derived? *Implemented default, confirm before the P3 promotion:* `uuid5(LISTING_NAMESPACE, "factorlab:listing:{EXCHANGE}:isin:{ISIN}")`, falling back to `…:sym:{symbol}` when there is no ISIN; issuer and security keep today's `isin:{ISIN}` derivation. ISIN-keyed listings survive renames; symbol-keyed ones (no ISIN) do not, and a rename creates a new listing unless an alias links it.
- **Q3.** Should `market.quotes` (90-day TTL) be a v1 dataset? The India batch-quote path already fetches the quote data.
- **Q4.** *Resolved (2026-09-24):* dataset only. The GitHub CSV and EODHD resolvers are sources of `ref.universe_membership`; bar runs take their listings from `universe_members`.
- **Q5.** Should `meta.source_status` gain an `instance` column, or should instance status keep living in `source_channel`?
