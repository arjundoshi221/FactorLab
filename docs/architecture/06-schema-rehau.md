# Schema Rehau — FactorLab v2

> Status: `[design]` — revision 2 (post-review)
> Last verified: 2026-09-19

Full redesign of the FactorLab ClickHouse footprint. Replaces the flat 19-table
`factorlab` database. Targets: (1) every research question ≤ 1–2 joins away,
(2) point-in-time correctness enforced by grants and CI, not convention,
(3) horizontal scale to G10 + APAC without new tables, (4) hedge-fund-grade
provenance and lineage.

This doc is the authoritative spec for what to build. `docs/architecture/02-database-clickhouse.md`
becomes the *why*; this doc is the *what*.

**Revision 2 changes** (2026-09-19): incorporated review findings F1, F4, F5,
F6, F7, F8, F9, F10, F12, F14. See section 14 for what was changed and why.
Deferred: F2 (data-quality-aware conflict resolution — will address in post-
ingest processing), F3 (FX complexity — using IBKR EOD snaps for exposure
currencies), F11 (futures roll methodology — deferred with `derived.*`), F13
(alt-data SLA), F15 (latency SLOs — separate ops/health module).

---

## 1. Design principles

1. **5NF for dimensions, wide facts.** Reference/dimension tables carry no
   redundant attributes. Fact tables deliberately denormalize hot attributes
   (country_code, sector, entity_id) onto every row so the common research
   question needs zero joins. This is Kimball star schema tuned for a columnar
   engine, not OLTP 5NF — which would be wrong for ClickHouse.

2. **Point-in-time (PIT) correctness is not optional.** Every fact row carries
   `event_time` (when the fact was true), `as_of_time` (earliest moment it was
   knowable to us), and `ingested_at` (when the row landed). Backtests filter on
   `as_of_time`. Restated fundamentals become new rows with a later `as_of_time`,
   never overwriting the prior view.

3. **Provenance-first.** Every row can be traced back to a `raw_id` (vendor
   payload) or a `computation_id` (code that produced it). No row lacks lineage.

4. **Cadence is a column, not a table.** OHLCV bars at any resolution live in
   one table with `resolution` in the sort key. Same for coverage, expected
   series, universes. Adding a resolution or a country never creates a table.

5. **Country is a column, not a table.** No `us_*` / `india_*` split. Adding
   Japan, Singapore, or the UK is a row addition to `ref.countries`. Every fact
   is `country_code`-scoped in its sort key so filtering is a partition prune.

6. **Canonical IDs everywhere.** `entity_id`, `security_id`, `listing_id` are
   FactorLab UUIDs assigned at resolution time. Vendor IDs live only in
   `ref.identifier_aliases`. Facts never carry vendor keys.

7. **No orphan tables.** Every row FKs to something canonical: a listing, a
   security, an entity, a source, an ingestion run, or a computation.

8. **Raw immutable, curated versioned, derived reproducible.**
   `raw.*` = byte-for-byte vendor payloads, immutable, `MergeTree`.
   Everything else = `ReplacingMergeTree(version)` for last-writer-wins on
   upstream corrections.

---

## 2. Namespace layout

Each is a separate ClickHouse database, allowing distinct storage policies,
retention rules, and access grants.

| Database | Purpose | Change velocity | Retention |
|---|---|---|---|
| `ref` | Dimensions: countries, exchanges, securities, listings, entities, aliases, universes | Slow (SCD-2) | Forever |
| `market` | Prices, quotes, corporate actions, FX | Append-heavy | Bars forever; quotes 90d |
| `fundamentals` | Filings, line items, snapshots, holdings, insider transactions | Batch-daily | Forever |
| `alt` | Political, social, research, satellite, card panel | Batch to real-time | Per source |
| `derived` | Factors, signals, portfolios, backtests | Recomputable | Recompute on demand |
| `ops` | Ingestion runs, coverage, source status, expected series, lineage | Continuous | 2y hot, 5y cold |
| `raw` | Raw HTTP + stream payload archive | Append-only, immutable | Per-source retention |

---

## 3. `ref` — Dimensions in 3NF

### `ref.countries`

```sql
country_code       FixedString(2)      -- ISO-3166 alpha-2, single source of truth
name               String
region             LowCardinality(String)   -- 'americas','emea','apac'
default_currency   FixedString(3)           -- ISO-4217 alpha-3
timezone           String                    -- IANA
active             Bool
first_active_at    Date
version            UInt64
ingested_at        DateTime64(3, 'UTC')
ORDER BY country_code
```

**Change from today:** `market_code` is removed. It was redundant with
`country_code`. One ISO-3166 code everywhere.

### `ref.currencies`

```sql
currency_code     FixedString(3)        -- ISO-4217
name              String
is_deliverable    Bool                   -- 'INR' is non-deliverable offshore
version, ingested_at
ORDER BY currency_code
```

### `ref.exchanges`

```sql
exchange_code     LowCardinality(String)   -- 'NASDAQ','NYSE','NSE','LSE','TSE','HKEX','SGX'
mic               FixedString(4)            -- ISO-10383 (XNAS, XNYS, XNSE)
name              String
country_code      FixedString(2)            -- FK ref.countries
currency_code     FixedString(3)            -- FK ref.currencies (native trading ccy)
timezone          String
sessions          String                    -- JSON: pre/regular/post windows
active            Bool
version, ingested_at
ORDER BY exchange_code
```

### `ref.sectors`

```sql
sector_id         String                    -- '10','1010','101010','10101010' (GICS 2/4/6/8)
level             UInt8                     -- 2,4,6,8
parent_id         Nullable(String)
name              String
classification    LowCardinality(String)    -- 'gics','icb','naics'
active            Bool
version, ingested_at
ORDER BY (classification, sector_id)
```

Same table serves GICS, ICB, NAICS — classification dimension distinguishes.

### `ref.sources`

```sql
source_id         LowCardinality(String)    -- 'schwab','upstox','eodhd','ibkr','sec_edgar',
                                             --   'house_clerk_ptr','fec','reddit','arxiv'
kind              LowCardinality(String)    -- 'market','fundamentals','political','social','research'
vendor            LowCardinality(String)
auth_type         LowCardinality(String)    -- 'oauth2','api_key','none','scraping'
countries         Array(FixedString(2))     -- ['US'], ['IN'], ['US','GB','JP',...] or ['*'] for global
active            Bool
notes             String
version, ingested_at
ORDER BY source_id
```

Every `source` in every fact/ops row FKs here.

### `ref.entities`

The **issuer / company** layer. LEI-keyed.

```sql
entity_id             UUID                        -- factorlab canonical
legal_name            String
lei                   Nullable(FixedString(20))   -- ISO-17442
country_of_domicile   FixedString(2)
country_of_incorp     FixedString(2)
active                Bool
first_seen, last_seen Date
version, ingested_at
ORDER BY entity_id
```

Corporate actions that change the entity (M&A, spinoffs) create new
entity rows with cross-references in `ref.entity_relationships`.

### `ref.entity_relationships`

```sql
parent_entity_id      UUID
child_entity_id       UUID
relationship          LowCardinality(String)   -- 'ma','spinoff','rename','subsidiary'
effective_date        Date
end_date              Nullable(Date)
notes                 String
version, ingested_at
ORDER BY (parent_entity_id, effective_date)
```

### `ref.securities`

The **claim / instrument** layer. ISIN-keyed.

```sql
security_id       UUID                              -- factorlab canonical
entity_id         UUID                              -- FK ref.entities
security_type     LowCardinality(String)            -- 'common','preferred','adr','etf','etn',
                                                     --   'reit','warrant','right','mutual_fund',
                                                     --   'index','future','option','bond','fx_pair'
isin              Nullable(FixedString(12))
cusip             Nullable(FixedString(9))
figi              Nullable(FixedString(12))
share_class       LowCardinality(String)            -- 'A','B','C','' (single class)
currency_code     FixedString(3)                    -- FK ref.currencies (denomination)
issue_date        Nullable(Date)
maturity_date     Nullable(Date)                    -- bonds, options, futures
sector_gics_id    Nullable(String)                  -- FK ref.sectors (classification='gics')
active            Bool
version, ingested_at
ORDER BY security_id
```

### `ref.listings`

The **venue-scoped tradable**. This is the primary `factorlab_id` for market data.

```sql
listing_id            UUID                          -- ★ factorlab canonical for market data ★
security_id           UUID                          -- FK ref.securities
exchange_code         LowCardinality(String)        -- FK ref.exchanges
country_code          FixedString(2)                -- denormalized from exchange
trading_symbol        LowCardinality(String)        -- 'AAPL', 'TCS', '7203'
local_symbol          Nullable(String)              -- exchange-native symbol if different
mic                   FixedString(4)                -- ISO-10383
lot_size              UInt32
tick_size             Nullable(Decimal(18,6))
is_primary            Bool                          -- primary listing for the security
first_traded          Nullable(Date)
last_traded           Nullable(Date)                -- delisting date
active                Bool
version, ingested_at
ORDER BY listing_id
```

### `ref.contracts`

The **derivative contract** layer. Options + futures.

```sql
contract_id           UUID                          -- factorlab canonical for the derivative
underlying_listing_id UUID                          -- FK ref.listings (underlying)
exchange_code         LowCardinality(String)
country_code          FixedString(2)
contract_type         LowCardinality(String)        -- 'future','call','put'
expiry                Date
strike                Nullable(Decimal(18,6))       -- options only
right                 LowCardinality(String)        -- 'C','P','' (futures)
exercise_style        LowCardinality(String)        -- 'american','european',''
multiplier            UInt32                        -- shares per contract
lot_size              UInt32
tick_size             Nullable(Decimal(18,6))
weekly                Bool
active                Bool
version, ingested_at
ORDER BY (underlying_listing_id, expiry, right, strike)
```

### `ref.identifier_aliases`

The universal resolver. Any vendor ID → canonical UUID.

```sql
alias_kind            LowCardinality(String)        -- 'ticker','isin','cusip','figi','lei',
                                                     --   'sedol','openfigi','bloomberg',
                                                     --   'schwab_conid','ibkr_conid','eodhd_symbol',
                                                     --   'upstox_instrument_key','fec_candidate_id',
                                                     --   'bioguide','cik','ric','permid'
alias_value           String
scope_country         Nullable(FixedString(2))      -- ticker namespaces are country-scoped
scope_exchange        Nullable(LowCardinality(String))  -- for MIC-scoped tickers
target_kind           LowCardinality(String)        -- 'entity','security','listing','contract'
target_id             UUID
source                LowCardinality(String)        -- who told us this
valid_from            Date
valid_to              Nullable(Date)                -- SCD-2 (ticker reassignment)
confidence            Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'manual_override'=5)
notes                 String
version, ingested_at
ENGINE = ReplacingMergeTree(version)
ORDER BY (alias_kind, alias_value, valid_from)
```

Every ingester resolves via this table. Not resolvable → falls back to
`ref.unresolved_identifiers` queue for manual review.

### `ref.listing_migrations`

Handles cases where the same real-world security moves venues (Alibaba
NYSE→HKEX ADR, dual-listing consolidation, delisting-relisting on pink sheets)
or where corporate events create a new `listing_id` that logically continues
an old one (spinoffs, reverse mergers). SCD-2 on `ref.identifier_aliases` only
covers ticker changes on the *same* listing; this table covers listing-to-listing
continuity for return-series construction.

```sql
CREATE TABLE ref.listing_migrations (
    migration_id       UUID,
    old_listing_id     UUID,                             -- FK ref.listings
    new_listing_id     UUID,                             -- FK ref.listings
    effective_date     Date,                             -- last trade date on old / first on new
    migration_type     LowCardinality(String),           -- 'venue_change','delisting_relisting',
                                                          --   'reverse_merger','spinoff','dual_listing_collapse'
    price_adjustment   Nullable(Decimal(18,6)),          -- ratio to apply to old-listing prices to align
    volume_adjustment  Nullable(Decimal(18,6)),
    source             LowCardinality(String),
    raw_id             Nullable(UUID),
    notes              String,
    as_of_time         DateTime64(3, 'UTC'),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (old_listing_id, effective_date, migration_id);
```

**Usage in return-series construction:** research view
`research.listing_continuous_returns` walks the migration chain to stitch
old-listing bars to new-listing bars, applying `price_adjustment`. Naive users
who read `market.bars` directly for a delisted-then-relisted name get bars
only for one leg; continuous-return work goes through the research view.

### `ref.corporate_actions`

```sql
action_id             UUID
security_id           UUID                          -- FK ref.securities
listing_id            Nullable(UUID)                -- if action is listing-scoped
action_type           LowCardinality(String)        -- 'split','dividend','spinoff','rights',
                                                     --   'delisting','ticker_change','merger'
announcement_date     Nullable(Date)
ex_date               Date                          -- primary sort key
record_date           Nullable(Date)
payable_date          Nullable(Date)
effective_date        Date
split_from            Nullable(UInt32)              -- 2 in a 2:1 split
split_to              Nullable(UInt32)              -- 1 in a 2:1 split
cash_amount           Nullable(Decimal(18,6))       -- dividend / cash-in-lieu
currency_code         Nullable(FixedString(3))
old_symbol            Nullable(String)              -- ticker change
new_symbol            Nullable(String)
notes                 String
source                LowCardinality(String)
raw_id                Nullable(UUID)
as_of_time            DateTime64(3, 'UTC')
version, ingested_at
ORDER BY (security_id, ex_date, action_id)
```

### `ref.adjustment_factors`

Materialized/computed daily-cumulative adjustment factors per listing.

```sql
listing_id            UUID
event_date            Date
price_factor          Decimal(18,10)                -- multiply raw prices by this to get adjusted
volume_factor         Decimal(18,10)                -- multiply raw volumes by this
computation_id        UUID                          -- lineage
computed_at           DateTime64(3, 'UTC')
ORDER BY (listing_id, event_date)
```

### `ref.universes` and `ref.universe_membership`

```sql
-- Universe definitions
CREATE TABLE ref.universes (
    universe_id    LowCardinality(String)     -- 'r3k','r1k','sp500','nifty50','nifty500','custom_watchlist'
    name           String
    provider       LowCardinality(String)     -- 'russell','msci','sp','nse','custom'
    rebalance_freq LowCardinality(String)     -- 'quarterly','annual','continuous'
    country_scope  Array(FixedString(2))
    active         Bool
    version, ingested_at
) ORDER BY universe_id;

-- Point-in-time membership (SCD-2)
CREATE TABLE ref.universe_membership (
    universe_id    LowCardinality(String)
    listing_id     UUID
    weight         Nullable(Float32)          -- for weighted indices
    effective_from Date                        -- inclusive
    effective_to   Nullable(Date)              -- inclusive; NULL = current
    reason         LowCardinality(String)      -- 'reconstitution','ipo','delisting','merger'
    source         LowCardinality(String)
    raw_id         Nullable(UUID)
    version, ingested_at
) ORDER BY (universe_id, effective_from, listing_id);
```

Backtests: `WHERE effective_from <= trade_date AND (effective_to IS NULL OR
effective_to > trade_date)` to get PIT constituents.

### `ref.dataset_versions`

Every time a curated dataset is materially rewritten (adjustment methodology
change, fundamentals tag-mapping revision, universe reconstruction rules),
record the version. Downstream compute (backtests, factor calcs) pins to
`dataset_version` for reproducibility.

```sql
CREATE TABLE ref.dataset_versions (
    dataset            LowCardinality(String),      -- 'market.bars_adjusted','fundamentals.snapshots_pit_eom',
                                                     --   'ref.universe_membership.r3k','ref.adjustment_factors'
    version_tag        LowCardinality(String),      -- 'v1','v2','v2.1'
    superseded_by      Nullable(LowCardinality(String)),
    materialized_from  DateTime64(3, 'UTC'),        -- when this version's data starts
    materialized_to    Nullable(DateTime64(3, 'UTC')),
    methodology_note   String,                       -- what changed vs prior version
    computation_id     Nullable(UUID),               -- FK derived.computations, if it was a full recompute
    active             Bool,
    created_at         DateTime64(3, 'UTC'),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (dataset, version_tag, materialized_from);
```

Backtest reports must log the `(dataset, version_tag)` tuple for every input.
Two backtests with different dataset versions cannot be compared apples-to-apples.

### `ref.sessions` and `ref.holidays`

```sql
CREATE TABLE ref.sessions (
    exchange_code  LowCardinality(String)
    session        LowCardinality(String)      -- 'pre','regular','post','open_auction','close_auction'
    day_of_week    UInt8                        -- 0-6
    starts_at      String                       -- 'HH:MM' local
    ends_at        String
    effective_from Date
    effective_to   Nullable(Date)
) ORDER BY (exchange_code, effective_from, session);

CREATE TABLE ref.holidays (
    country_code   FixedString(2),
    exchange_code  Nullable(LowCardinality(String)),  -- exchange-specific holidays
    holiday_date   Date,
    name           String,
    market_state   LowCardinality(String),      -- 'closed','early_close','late_open',
                                                 --   'muhurat_only','open_only'
    override_open  Nullable(String),             -- 'HH:MM' local, if late open
    override_close Nullable(String),             -- 'HH:MM' local, if early close
    override_pre_close Nullable(String),
    override_post_open Nullable(String),
    source         LowCardinality(String),
    version, ingested_at
) ORDER BY (country_code, holiday_date);

-- Effective session windows resolver (view, not table).
-- Given (exchange_code, trade_date) → returns (open_time, close_time, expected_bars_1min).
-- Composes ref.sessions with ref.holidays overrides.
CREATE VIEW ref.session_windows_effective AS
SELECT
    e.exchange_code,
    d.trade_date,
    s.session,
    coalesce(h.override_open,  s.starts_at)  AS starts_at,
    coalesce(h.override_close, s.ends_at)    AS ends_at,
    dateDiff('minute',
        parseDateTimeBestEffort(concat(toString(d.trade_date), ' ', coalesce(h.override_open, s.starts_at))),
        parseDateTimeBestEffort(concat(toString(d.trade_date), ' ', coalesce(h.override_close, s.ends_at)))
    ) AS expected_bars_1min,
    coalesce(h.market_state, 'regular') AS market_state
FROM ref.exchanges e
CROSS JOIN (SELECT toDate('2000-01-01') + number AS trade_date FROM numbers(50000)) d
LEFT JOIN ref.sessions s
    ON s.exchange_code = e.exchange_code
   AND s.day_of_week = toDayOfWeek(d.trade_date) - 1
   AND s.effective_from <= d.trade_date
   AND (s.effective_to IS NULL OR s.effective_to > d.trade_date)
LEFT JOIN ref.holidays h
    ON h.exchange_code = e.exchange_code AND h.holiday_date = d.trade_date
WHERE s.session IS NOT NULL AND coalesce(h.market_state, '') != 'closed';
```

**Required holiday data at launch:**

*United States (XNYS, XNAS, ARCX):*
- All full-closure federal holidays
- **Early closes:** day after Thanksgiving (13:00 ET), Christmas Eve when a
  trading day (13:00 ET), day before Independence Day when a trading day (13:00 ET)
- Reg NMS extended-hours affected — pre 04:00, regular 09:30–close, post 20:00

*India (XNSE, XBOM):*
- Full-closure holidays per NSE circular (Republic Day, Independence Day, Diwali
  Balipratipada, Gandhi Jayanti, etc.) — ~15 per year
- **Muhurat trading:** Diwali Lakshmi Puja evening session, typically
  18:15–19:15 IST — logged as `market_state='muhurat_only'` with override times
- Occasional early closes for specific events (rare)

**Downstream consumers:** `ops.session_coverage.expected` must be computed
from `ref.session_windows_effective`, not hardcoded to 390 (US) or 375 (NSE).
Otherwise coverage dashboards mark early-close days as 66% coverage
(210 actual / 390 expected).

**Source of truth for backfill:** the `exchange-calendars` Python package
(already a dependency) has XNYS and XBOM calendars — use as authoritative for
regular sessions and holiday dates. Muhurat and early-close *times* need
manual override rows because the package doesn't encode session boundaries
for partial days.

---

## 4. `market` — Facts, wide and denormalized

### `market.bars`

The primary market data table. All linear products, all resolutions,
all countries, all vendors.

```sql
CREATE TABLE market.bars (
    -- identity --
    country_code       FixedString(2),
    listing_id         UUID,
    security_id        UUID,                          -- denormalized
    entity_id          UUID,                          -- denormalized (may be NULL for indices)
    product_type       LowCardinality(String),        -- 'common','preferred','adr','etf','etn','reit',
                                                       --   'warrant','index','index_future',
                                                       --   'single_stock_future','vol_future','vol_etp'
    -- time & shape --
    resolution         LowCardinality(String),        -- '5s','1min','5min','15min','1h','daily','weekly','monthly'
    session            LowCardinality(String),        -- 'regular','pre','post','open_auction','close_auction'
    bar_time           DateTime64(3, 'UTC'),          -- bar OPEN time (convention)
    trade_date         Date,                          -- session-local date, denormalized for pruning
    -- OHLC + volume --
    open, high, low, close   Nullable(Decimal(18,6)),
    volume             Nullable(UInt64),               -- shares (spot) OR contracts (derivative)
    turnover           Nullable(Decimal(24,4)),        -- listing-currency notional
    trades_count       Nullable(UInt32),
    -- derivatives-only --
    oi                 Nullable(UInt64),               -- open interest, contracts
    settlement_price   Nullable(Decimal(18,6)),        -- exchange settlement
    -- cross-product-safe --
    notional_usd       Nullable(Decimal(24,4))         -- volume × close × multiplier × fx_to_usd
                                                       --   computed at read time via MV, not stored here
    -- provenance --
    source             LowCardinality(String),
    source_channel     LowCardinality(String),
    raw_id             Nullable(UUID),
    ingest_run_id      UUID,
    -- audit --
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    latency_ms         Nullable(Int32),
    version            UInt64,
    -- skip indices: session and source_channel are commonly filtered but low-cardinality,
    -- so they earn a skip index instead of sort-key positions.
    INDEX idx_session         session         TYPE set(8)               GRANULARITY 4,
    INDEX idx_source_channel  source_channel  TYPE set(32)              GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (product_type, resolution, toYYYYMM(bar_time))
ORDER BY (country_code, product_type, listing_id, resolution, source, bar_time);
```

**Conventions:**
- `bar_time` is bar OPEN in UTC.
- `trade_date` is session-local date, denormalized for pruning.
- Volume: shares for spot, contracts for futures. **Never sum across `product_type`.**
- `notional_usd` is computed by a materialized view, not stored on raw rows.
  A raw `notional_usd` would be frozen at ingest-time FX; the MV re-derives on read.
- **Sort key kept to 6 columns** (was 8 in v1). `session` and `source_channel`
  are filter-frequent but low-cardinality; using skip indices instead of sort-
  key positions keeps insert throughput high and the primary index small.

### `market.bars_adjusted` — materialized view

```sql
CREATE MATERIALIZED VIEW market.bars_adjusted TO market.bars_adjusted_storage AS
SELECT
    b.*,
    b.open  * af.price_factor  AS adj_open,
    b.high  * af.price_factor  AS adj_high,
    b.low   * af.price_factor  AS adj_low,
    b.close * af.price_factor  AS adj_close,
    b.volume / af.volume_factor AS adj_volume
FROM market.bars b
LEFT JOIN ref.adjustment_factors af
    ON af.listing_id = b.listing_id AND af.event_date = b.trade_date
WHERE b.product_type IN ('common','preferred','adr','etf','etn','reit');
```

Non-linear products (futures, options) don't get split-adjusted; futures
have their own continuous-contract handling (see `market.futures_continuous`).

### `market.options_bars`

```sql
CREATE TABLE market.options_bars (
    -- identity --
    country_code           FixedString(2),
    contract_id            UUID,                     -- FK ref.contracts
    underlying_listing_id  UUID,
    underlying_security_id UUID,
    -- option key (denormalized) --
    expiry                 Date,
    strike                 Decimal(18,6),
    right                  LowCardinality(String),   -- 'C' / 'P'
    exercise_style         LowCardinality(String),
    multiplier             UInt32,
    -- time & shape --
    resolution, session, bar_time, trade_date,       -- same conventions as bars
    -- OHLCV --
    open, high, low, close   Nullable(Decimal(18,6)),
    volume, oi               Nullable(UInt64),
    turnover                 Nullable(Decimal(24,4)),
    -- greeks + IV --
    iv                       Nullable(Float64),
    delta, gamma, vega, theta, rho   Nullable(Float64),
    underlying_price         Nullable(Decimal(18,6)),  -- for reproducibility
    -- provenance + audit (same as bars) --
    source, source_channel, raw_id, ingest_run_id,
    as_of_time, ingested_at, latency_ms, version
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (toYYYYMM(expiry), toYYYYMM(bar_time))
ORDER BY (country_code, underlying_listing_id, expiry, right, strike, resolution, bar_time);
```

### `market.futures_continuous`

Rolled continuous futures series (front, back, calendar-spread). Derived from
`market.bars` + roll rules.

```sql
CREATE TABLE market.futures_continuous (
    country_code       FixedString(2),
    underlying_listing_id UUID,                       -- 'the front NIFTY future' concept
    roll_method        LowCardinality(String),        -- 'volume','open_interest','first_notice','custom_5d_before_expiry'
    roll_position      LowCardinality(String),        -- 'c1','c2','c3','...'  (front, back, ...)
    resolution         LowCardinality(String),
    session            LowCardinality(String),
    bar_time           DateTime64(3, 'UTC'),
    trade_date         Date,
    active_contract_id UUID,                          -- which contract this row came from
    open, high, low, close   Nullable(Decimal(18,6)),
    volume, oi         Nullable(UInt64),
    roll_adjustment    Decimal(18,6),                 -- cumulative price adjustment applied
    computation_id     UUID,
    computed_at        DateTime64(3, 'UTC'),
    as_of_time         DateTime64(3, 'UTC'),
    version            UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (roll_method, toYYYYMM(bar_time))
ORDER BY (country_code, underlying_listing_id, roll_method, roll_position, bar_time);
```

### `market.quotes` (built when execution enters scope)

```sql
CREATE TABLE market.quotes (
    country_code       FixedString(2),
    listing_id         UUID,
    security_id        UUID,
    entity_id          UUID,
    observed_at        DateTime64(6, 'UTC'),
    -- last trade at observation time --
    last_price         Nullable(Decimal(18,6)),
    last_size          Nullable(UInt32),
    last_trade_at      Nullable(DateTime64(6, 'UTC')),
    -- book state --
    bid, ask           Nullable(Decimal(18,6)),
    bid_size, ask_size Nullable(UInt32),
    -- day accumulators (from vendor) --
    day_open, day_high, day_low, day_vwap  Nullable(Decimal(18,6)),
    day_volume         Nullable(UInt64),
    day_turnover       Nullable(Decimal(24,4)),
    -- context --
    session            LowCardinality(String),
    -- futures snapshots --
    oi                 Nullable(UInt64),
    settlement_price   Nullable(Decimal(18,6)),
    -- provenance --
    source, source_channel, raw_id, ingest_run_id,
    ingested_at, latency_ms, version
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(observed_at)
ORDER BY (country_code, listing_id, source, observed_at)
TTL toDate(observed_at) + INTERVAL 90 DAY;
```

### `market.fx_rates`

```sql
CREATE TABLE market.fx_rates (
    base_currency      FixedString(3),
    quote_currency     FixedString(3),
    resolution         LowCardinality(String),        -- '1min','1h','daily'
    fix_convention     LowCardinality(String),        -- 'spot','wm_reuters_4pm_london','ecb','close'
    bar_time           DateTime64(3, 'UTC'),
    trade_date         Date,
    open, high, low, close   Nullable(Decimal(18,10)),
    source, source_channel, raw_id, ingest_run_id,
    as_of_time, ingested_at, version
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(bar_time)
ORDER BY (base_currency, quote_currency, resolution, fix_convention, bar_time);
```

### `market.trades` and `market.orderbook_l2` — deferred

Reserve names; do not build until microstructure research or execution
demands them. Different storage tier (much bigger).

---

## 5. `fundamentals` — Point-in-time accounting

### `fundamentals.filings`

```sql
CREATE TABLE fundamentals.filings (
    filing_id          UUID,
    entity_id          UUID,                          -- FK ref.entities
    form_type          LowCardinality(String),        -- '10-K','10-Q','8-K','20-F','40-F','NPORT-P','13F-HR','13F-NT','SC 13G','4','3','5'
    filing_date        Date,                          -- when filed with regulator
    period_end         Nullable(Date),                -- reporting period end (for 10-K/Q)
    period_type        LowCardinality(String),        -- 'annual','quarterly','ytd','ttm','other'
    fiscal_year        Nullable(UInt16),
    fiscal_period      Nullable(LowCardinality(String)),  -- 'Q1','Q2','Q3','Q4','FY'
    filed_at           DateTime64(3, 'UTC'),          -- exact filing timestamp
    accepted_at        DateTime64(3, 'UTC'),          -- when regulator accepted
    accession_number   String,                        -- SEC-native
    amends_filing_id   Nullable(UUID),                -- immediate predecessor in restatement chain
    original_filing_id Nullable(UUID),                -- root of restatement chain (self if no amendments)
                                                       --   materialized at ingest: O(1) chain-root lookup
                                                       --   without recursive CTE
    amendment_seq      UInt8 DEFAULT 0,               -- 0 = original, 1 = first amendment, ...
    is_amended         Bool,                           -- has been superseded by a later filing
    is_amendment       Bool,                           -- this filing itself is an amendment
    filing_url         String,
    source             LowCardinality(String),        -- 'sec_edgar','eodhd','simplywall'
    raw_id             Nullable(UUID),
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    version            UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(filing_date)
ORDER BY (entity_id, filed_at, filing_id);
```

**Restatement chain conventions:**
- Original filing: `original_filing_id = filing_id` (self), `amends_filing_id = NULL`,
  `amendment_seq = 0`, `is_amendment = false`.
- First amendment: `amends_filing_id` = original, `original_filing_id` = original,
  `amendment_seq = 1`, `is_amendment = true`.
- Second amendment: `amends_filing_id` = first amendment, `original_filing_id` =
  original, `amendment_seq = 2`.
- `original_filing_id` is materialized at ingest by looking up `amends_filing_id`'s
  own `original_filing_id`. O(1) chain-root queries; no recursive CTE.
- To answer "all amendments to Q3 2024 net income for Apple":
  `WHERE original_filing_id = <root> ORDER BY amendment_seq`.

### `fundamentals.line_items` — tall/long

```sql
CREATE TABLE fundamentals.line_items (
    filing_id          UUID,
    entity_id          UUID,
    tag                LowCardinality(String),        -- 'us-gaap:Revenues','us-gaap:NetIncomeLoss',
                                                       --   or standardized 'revenue','net_income',
                                                       --   'ebitda','fcf','shares_diluted'
    tag_standard       LowCardinality(String),        -- 'us-gaap','ifrs','factorlab_standardized'
    statement          LowCardinality(String),        -- 'income','balance','cashflow','equity_changes','notes'
    period_end         Date,
    period_start       Nullable(Date),                -- for range items (Q revenue: start=Apr 1, end=Jun 30)
    period_type        LowCardinality(String),        -- 'point','duration'
    value              Nullable(Decimal(38,4)),
    currency_code      Nullable(FixedString(3)),
    unit               LowCardinality(String),        -- 'currency','shares','ratio','count','usd_per_share'
    context_ref        String,                         -- XBRL context
    dimensions         String,                         -- XBRL segments as JSON
    source             LowCardinality(String),
    raw_id             Nullable(UUID),
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    version            UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (statement, toYYYYMM(period_end))
ORDER BY (entity_id, tag, period_end, filing_id);
```

Long/tall shape → adding a new tag is a row insert, not a schema change.

### PIT snapshot access — two-tier

The naive `MATERIALIZED VIEW` approach fails: `argMax ... FILTER (WHERE)` is
not ClickHouse syntax, and an MV cannot precompute an `asof_date`-parameterized
view because asof is a query input, not table data. Solution is two-tier:

**Tier 1: `fundamentals.snapshots_pit_eom` — scheduled physical table**

A monthly job materializes end-of-month snapshots. Standard cadence for
monthly-frequency backtests (Fama-French style). Fast: physical table with
period wide-format.

```sql
CREATE TABLE fundamentals.snapshots_pit_eom (
    entity_id          UUID,
    period_end         Date,                          -- reporting period end
    period_type        LowCardinality(String),
    asof_date          Date,                          -- EOM this snapshot represents
    -- ~200 standardized tags as Nullable columns --
    revenue            Nullable(Decimal(38,4)),
    net_income         Nullable(Decimal(38,4)),
    ebitda             Nullable(Decimal(38,4)),
    ebit               Nullable(Decimal(38,4)),
    cogs               Nullable(Decimal(38,4)),
    opex               Nullable(Decimal(38,4)),
    total_assets       Nullable(Decimal(38,4)),
    total_debt         Nullable(Decimal(38,4)),
    cash_and_equiv     Nullable(Decimal(38,4)),
    shareholders_equity Nullable(Decimal(38,4)),
    cfo                Nullable(Decimal(38,4)),       -- cash flow from ops
    capex              Nullable(Decimal(38,4)),
    fcf                Nullable(Decimal(38,4)),
    shares_diluted     Nullable(Decimal(38,4)),
    -- ... (see docs/fundamentals/standardized_tags.md for full list, TBD)
    -- lineage --
    computation_id     UUID,
    dataset_version    LowCardinality(String),        -- FK ref.dataset_versions
    computed_at        DateTime64(3, 'UTC'),
    version            UInt64
) ENGINE = ReplacingMergeTree(version)
PARTITION BY (toYYYYMM(asof_date), toYYYY(period_end))
ORDER BY (entity_id, period_end, period_type, asof_date);
```

Populated by a scheduled job (`derived.computations` records each run) that
runs on the first business day after month-end, for the just-closed month's
EOM asof_date. Prior months are immutable snapshots — never rewritten unless
the underlying tag mapping changes (which triggers a new `dataset_version`).

**Tier 2: `research.fundamentals_pit` — on-the-fly view for arbitrary asof**

For daily backtests or ad-hoc research where EOM cadence isn't enough:

```sql
CREATE VIEW research.fundamentals_pit AS
SELECT
    entity_id,
    period_end,
    period_type,
    argMaxIf(value, filed_at, tag = 'revenue')     AS revenue,
    argMaxIf(value, filed_at, tag = 'net_income')  AS net_income,
    argMaxIf(value, filed_at, tag = 'ebitda')      AS ebitda,
    -- ... 200 tags via argMaxIf pattern
    max(filed_at) AS latest_filed_at,
    max(as_of_time) AS latest_as_of
FROM fundamentals.line_items
WHERE filed_at <= {asof_date:Date}
GROUP BY entity_id, period_end, period_type;
```

Slower per-query (aggregates over `line_items`) but PIT-correct for any date.
Backtests default to EOM table; ad-hoc research uses the view.

**Tag standardization:** `fundamentals.line_items.tag_standard = 'factorlab_standardized'`
rows are the canonical set used by snapshots. Vendor tags (`us-gaap`, `ifrs`)
land as `line_items` rows too; a mapping job produces standardized rows.
Mapping revisions bump `ref.dataset_versions('fundamentals.snapshots_pit_eom')`.

### `fundamentals.consensus_estimates`, `holdings_13f`, `holdings_nport`, `insider_form4`

Same pattern: filing header + line items + optional PIT snapshot MV.

---

## 6. `alt` — Generalized alt-data pattern

Every alt table follows the same shape: identity + event_time + as_of_time +
source + raw_id + entity/security resolution + payload.

### `alt.political_trades`

```sql
CREATE TABLE alt.political_trades (
    trade_id             UUID,                        -- factorlab canonical
    country_code         FixedString(2),
    chamber              LowCardinality(String),      -- 'house','senate'
    filing_id            String,                      -- vendor's filing id
    filing_date          Date,
    transaction_date     Date,
    notification_date    Nullable(Date),
    bioguide_id          Nullable(String),
    legislator_entity_id UUID,                        -- resolved to ref.entities (individual entity)
    -- resolved instrument --
    ticker_raw           Nullable(String),            -- what the filing said
    listing_id           Nullable(UUID),              -- resolved via ref.identifier_aliases
    security_id          Nullable(UUID),
    entity_id            Nullable(UUID),              -- target-company entity
    resolution_confidence Enum8(...) DEFAULT 'unresolved',
    -- transaction --
    owner_code           LowCardinality(String),      -- 'self','spouse','child','joint'
    transaction_type     LowCardinality(String),      -- 'purchase','sale','exchange','partial_sale'
    asset_type_code      LowCardinality(String),      -- 'ST','OP','MF','GS','CS','BD'
    amount_min           Nullable(UInt64),
    amount_max           Nullable(UInt64),
    amount_str           String,
    -- provenance --
    source, raw_id, ingest_run_id,
    as_of_time, ingested_at, version
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(transaction_date)
ORDER BY (country_code, transaction_date, entity_id, bioguide_id, trade_id);
```

### `alt.political_filings`, `alt.political_committee_memberships`, `alt.political_lobbying`, `alt.political_contracts`, `alt.political_fec_contributions`

Each follows the pattern above with source-specific columns.

### `alt.social_reddit_posts`, `alt.social_x_posts`

```sql
CREATE TABLE alt.social_reddit_posts (
    post_id              String,                       -- vendor-native
    country_code         FixedString(2),
    subreddit            LowCardinality(String),
    posted_at            DateTime64(3, 'UTC'),
    author               String,
    title                String,
    body                 String,
    score                Int32,
    comment_count        UInt32,
    -- mentioned tickers (multi-value, resolved) --
    mentioned_listings   Array(UUID),                  -- listing_ids resolved from body
    mentioned_entities   Array(UUID),
    ticker_confidence    Array(Enum8(...)),
    -- sentiment (if pre-computed) --
    sentiment_score      Nullable(Float32),
    sentiment_source     LowCardinality(String),
    source, raw_id, ingest_run_id,
    as_of_time, ingested_at, version
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(posted_at)
ORDER BY (country_code, subreddit, posted_at, post_id);
```

### `alt.research_arxiv`, `alt.satellite_*`, `alt.card_panel_*`

Reserve names; build when data flows.

---

## 7. `derived` — Reproducible computed data

### `derived.computations`

```sql
CREATE TABLE derived.computations (
    computation_id       UUID,
    dataset              LowCardinality(String),      -- 'factor_momentum_12_1','signal_value_composite'
    code_version         LowCardinality(String),      -- git sha
    input_query          String,                       -- SQL of what it read
    input_hash           FixedString(64),
    input_row_count      UInt64,
    parameters           String,                       -- JSON
    row_count            UInt64,
    started_at           DateTime64(3, 'UTC'),
    completed_at         Nullable(DateTime64(3, 'UTC')),
    status               LowCardinality(String),
    error                Nullable(String),
    version, ingested_at
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(started_at)
ORDER BY (dataset, code_version, started_at, computation_id);
```

### `derived.factors`

```sql
CREATE TABLE derived.factors (
    country_code         FixedString(2),
    listing_id           UUID,
    security_id          UUID,
    entity_id            UUID,
    sector_id            LowCardinality(String),      -- denormalized
    factor_id            LowCardinality(String),      -- 'momentum_12_1','value_ep','quality_roic'
    factor_definition_hash FixedString(64),          -- guards against formula drift
    event_date           Date,
    value                Nullable(Float64),
    rank_pct             Nullable(Float32),            -- cross-sectional percentile in universe
    universe_id          LowCardinality(String),      -- context of rank
    -- lineage --
    computation_id       UUID,                         -- FK derived.computations
    code_version         LowCardinality(String),
    input_hash           FixedString(64),
    computed_at          DateTime64(3, 'UTC'),
    as_of_time           DateTime64(3, 'UTC'),
    version              UInt64,
    INDEX idx_listing listing_id TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (factor_id, toYYYYMM(event_date))
ORDER BY (country_code, factor_id, universe_id, event_date, listing_id);
```

**Sort key rationale:** date moves before `listing_id` so "as-of-date across
universe" queries (the dominant pattern for cross-sectional factor rankings)
prune tightly. Per-listing lookups still fast via the bloom filter skip index.

### `derived.signals`, `derived.portfolios`, `derived.backtest_runs`, `derived.backtest_returns`

Same pattern.

---

## 8. `ops` — Operational

### `ops.ingestion_runs`

```sql
CREATE TABLE ops.ingestion_runs (
    run_id               UUID,
    country_code         LowCardinality(String),      -- '*' for global
    pipeline             LowCardinality(String),
    source               LowCardinality(String),
    source_channel       LowCardinality(String),
    universe_id          LowCardinality(String),
    status               LowCardinality(String),
    started_at           DateTime64(3, 'UTC'),
    completed_at         Nullable(DateTime64(3, 'UTC')),
    requested_series     UInt32,
    successful_series    UInt32,
    failed_series        UInt32,
    rows_written         UInt64,
    listing_ids_touched  Array(UUID),                  -- which listings this batch wrote to
    error                Nullable(String),
    metadata_json        String,
    parent_run_id        Nullable(UUID),               -- for cascading pipelines
    version, ingested_at
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(started_at)
ORDER BY (country_code, pipeline, source, started_at, run_id);
```

### `ops.expected_series`, `ops.session_coverage`, `ops.recovery_state`, `ops.source_status`

Unified across countries (was `us_*` + `india_*`). All FK `listing_id`.

### `ops.lineage`

```sql
CREATE TABLE ops.lineage (
    dataset              LowCardinality(String),      -- 'market.bars','fundamentals.line_items','derived.factors'
    row_business_key     String,                       -- hash of (listing_id, resolution, bar_time, source) etc.
    source_raw_ids       Array(UUID),
    computation_id       Nullable(UUID),
    produced_at          DateTime64(3, 'UTC'),
    produced_by          LowCardinality(String),      -- 'ingest','backfill','recovery','recompute'
)
ENGINE = MergeTree
PARTITION BY (dataset, toYYYYMM(produced_at))
ORDER BY (dataset, row_business_key, produced_at)
TTL toDate(produced_at) + INTERVAL 90  DAY  DELETE WHERE startsWith(dataset, 'market.'),
    toDate(produced_at) + INTERVAL 2   YEAR DELETE WHERE startsWith(dataset, 'alt.');
    -- fundamentals.* and derived.* rows keep forever (much lower volume).
```

**Retention rationale:** market.bars produces ~4.7B lineage rows/yr at target
scale — retaining forever would double storage cost for information you
almost never query outside the last 30 days (anomaly diagnosis). Alt data
retention is 2y for the same reason at smaller scale. Fundamentals + derived
have low enough volume that permanent retention is trivial and directly
supports reproducibility.

**Alternative worth considering:** instead of per-row lineage for market.bars,
rely on `ops.ingestion_runs.listing_ids_touched` for run-level lineage.
Per-row lineage becomes opt-in only for datasets where it earns its keep
(fundamentals, derived).

---

## 9. `raw` — Immutable archive

### `raw.archive` (renamed from `raw_http_archive`)

```sql
CREATE TABLE raw.archive (
    raw_id               UUID,
    source               LowCardinality(String),
    source_channel       LowCardinality(String),
    transport            LowCardinality(String),      -- 'http','websocket','tcp_socket','grpc','file','sftp'
    country_code         Nullable(FixedString(2)),    -- NULL for global payloads (GLEIF, SEC EDGAR whole-market)
    source_url           String,
    request_key          String,                       -- vendor-native fetch key (symbol, batch id, url tail)
    status_code          Nullable(UInt16),
    response_headers     String,
    response_body        String CODEC(ZSTD(6)),
    content_type         LowCardinality(String),
    content_encoding     LowCardinality(String),
    response_sha256      FixedString(64),
    -- streaming batching --
    fetched_at           DateTime64(3, 'UTC'),        -- for streams: window_end_at
    window_start_at      Nullable(DateTime64(3, 'UTC')),
    event_count          Nullable(UInt32),
    -- audit --
    as_of_time           DateTime64(3, 'UTC'),
    metadata_json        String,
    -- skip indices --
    INDEX idx_request_key request_key      TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_sha256      response_sha256  TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY (source, coalesce(country_code, toFixedString('**', 2)), toYYYYMM(fetched_at))
ORDER BY (source, country_code, source_channel, fetched_at, raw_id)
SETTINGS storage_policy = 'raw_archive';
```

**Partitioning + retention:** `(source, country_code, YYYYMM)` lets you set
per-source-per-country retention. E.g., US market raw kept 2y, India raw kept
5y, GLEIF (global) kept 10y. Retention is enforced via nightly `ALTER TABLE
DROP PARTITION` from a cron job driven by policy config, not by ClickHouse TTL
(TTL doesn't compose cleanly with partition boundaries when policy varies).

**Skip indices:**
- `idx_request_key` — finds "all raw fetches for AAPL query" in ~ms via bloom prune
- `idx_sha256` — dedup checks ("have we already stored this exact payload?")

---

## 10. Cross-cutting concerns

### 10.1 Multi-vendor conflict resolution

Same (`listing_id`, `resolution`, `bar_time`) can arrive from Schwab, EODHD,
IBKR. Sort key includes `source` → no PK collision. To get "the best" bar:

```sql
CREATE MATERIALIZED VIEW market.bars_best TO market.bars_best_storage AS
SELECT
    country_code, listing_id, resolution, session, bar_time,
    argMax(open,   priority_score) AS open,
    argMax(high,   priority_score) AS high,
    argMax(low,    priority_score) AS low,
    argMax(close,  priority_score) AS close,
    argMax(volume, priority_score) AS volume,
    argMax(source, priority_score) AS chosen_source
FROM (
    SELECT b.*,
        arrayIndexOf(['ibkr','eodhd','schwab','upstox'], source) AS priority_score
    FROM market.bars b
)
GROUP BY country_code, listing_id, resolution, session, bar_time;
```

Priority list is a table (`ref.source_priorities`) so it's editable without
code deploys.

### 10.2 Time zones

- **Storage:** everything in `market.*`, `fundamentals.*`, `alt.*`, `ops.*` is UTC.
- **Bar time:** UTC always. Daily bars use `00:00:00 UTC of trade_date`.
- **Trade date:** session-local calendar date (e.g., 2026-09-18 for a NYSE session even if UTC crosses).
- **Session windows:** stored in `ref.sessions` as local-time strings; resolved to UTC at query time using `ref.holidays` for adjustments.

### 10.3 Corporate actions

- Raw prices in `market.bars` are **never** adjusted post-hoc.
- Split/dividend arrives → new row in `ref.corporate_actions` → nightly job
  recomputes `ref.adjustment_factors` → `market.bars_adjusted` MV serves
  research.
- Every research query defaults to reading `bars_adjusted`. Execution reads
  `bars` (raw) because live trading uses live prices.

### 10.4 PIT correctness — bitemporal, enforced

Two time axes on every fact row:
- **valid_time** = `event_time`, `bar_time`, `transaction_date`, `period_end`
  (when the fact was true in the world)
- **transaction_time** = `as_of_time`, `filed_at`, `ingested_at`
  (when the fact was knowable to us)

Backtests must filter on **transaction_time** to avoid look-ahead bias.
Enforcement is by construction — not convention. Four defenses stack:

#### 10.4.1 Grant separation

```sql
CREATE ROLE factorlab_research;
CREATE ROLE factorlab_admin;
CREATE ROLE factorlab_ingest;

-- Research role: read research.* views only. Cannot touch raw fact tables.
GRANT SELECT ON research.* TO factorlab_research;
REVOKE ALL ON market.*        FROM factorlab_research;
REVOKE ALL ON fundamentals.*  FROM factorlab_research;
REVOKE ALL ON alt.*           FROM factorlab_research;
REVOKE ALL ON derived.*       FROM factorlab_research;

-- Ingest role: writes fact tables, reads dimensions.
GRANT SELECT, INSERT ON market.*, fundamentals.*, alt.* TO factorlab_ingest;
GRANT SELECT ON ref.* TO factorlab_ingest;

-- Admin role: everything (deployment, backfill, migration).
GRANT ALL ON *.* TO factorlab_admin;
```

Research users physically cannot bypass PIT because they physically cannot
`SELECT * FROM market.bars`. They can only reach the data through
`research.*` views, which require an `asof_date` parameter.

#### 10.4.2 The `research.*` schema — parametric wrappers

Every fact table gets a research wrapper enforcing `as_of_time <= asof_date`.
ClickHouse parametric views make asof mandatory:

```sql
CREATE VIEW research.bars AS
SELECT * FROM market.bars
WHERE as_of_time <= {asof_date:Date};

CREATE VIEW research.bars_adjusted AS
SELECT * FROM market.bars_adjusted
WHERE as_of_time <= {asof_date:Date};

CREATE VIEW research.fundamentals_pit AS
SELECT ... FROM fundamentals.line_items
WHERE filed_at <= {asof_date:Date}
GROUP BY entity_id, period_end, period_type;

CREATE VIEW research.political_trades AS
SELECT * FROM alt.political_trades
WHERE as_of_time <= {asof_date:Date};

CREATE VIEW research.universe_membership AS
SELECT universe_id, listing_id, weight, effective_from, effective_to
FROM ref.universe_membership
WHERE effective_from <= {asof_date:Date}
  AND (effective_to IS NULL OR effective_to > {asof_date:Date});
```

Backtest usage always threads `asof_date`:

```sql
-- ✓ correct: asof is the trade decision date
SELECT close FROM research.bars_adjusted(asof_date = '2024-06-15')
WHERE listing_id = ... AND trade_date <= '2024-06-14';

-- ✗ impossible: raw table not accessible to research role
SELECT close FROM market.bars_adjusted WHERE trade_date <= '2024-06-14';
```

#### 10.4.3 CI enforcement

Repository CI runs a check that fails PRs if any `.py`, `.sql`, or `.ipynb`
outside allowed paths references raw fact-table schemas:

```bash
# scripts/ci/enforce_pit.sh
FORBIDDEN='(FROM|JOIN)\s+(factorlab\.)?(market|fundamentals|alt|derived)\.'
ALLOWED_PATHS='^(src/factorlab/storage/|src/factorlab/ingest/|migrations/|scripts/ops/)'
git diff --name-only origin/main | \
  grep -E '\.(py|sql|ipynb)$' | \
  grep -Ev "$ALLOWED_PATHS" | \
  xargs -I {} grep -lE "$FORBIDDEN" {} 2>/dev/null | \
  tee pit_violations.txt
[ -s pit_violations.txt ] && { echo "PIT violations found; use research.* views"; exit 1; }
```

Exception list is a hand-maintained `research/PIT_EXCEPTIONS.txt` — every
line must reference an approved reason. Exceptions require reviewer signoff
in the PR.

#### 10.4.4 PR review template (`.github/pull_request_template.md`)

Every PR touching research/factor/backtest code must complete this checklist
before merge:

```markdown
## PIT compliance
- [ ] All fact-table reads go through `research.*` views
- [ ] `asof_date` is threaded from backtest configuration, not hardcoded
- [ ] `asof_date` is the trade DECISION date, not the fill date or the
      reporting date
- [ ] Restatement / amendment cases considered — did the filing exist
      as-of `asof_date`?
- [ ] Universe membership filtered by `effective_from`/`effective_to`
      against `asof_date` — no forward-looking constituents
- [ ] Corporate actions (splits/divs) applied only if `ex_date <= asof_date`
- [ ] Any raw-table access (`market.*`, `fundamentals.*`, `alt.*`,
      `derived.*`) is documented in `research/PIT_EXCEPTIONS.txt` with
      justification
```

A reviewer must sign off explicitly on PIT compliance for any PR that adds
or modifies a research query.

#### 10.4.5 What NOT to enforce

- Ad-hoc research notebooks in `playground/` are exempted from the CI check
  (developers explore raw data there). They also cannot ship to production —
  the CI check runs on `src/`, `scripts/`, `migrations/`, and `notebooks/`
  reachable from the PR.
- Ops dashboards and admin scripts have their own path exemption because
  they legitimately query raw ingestion state for health monitoring.

### 10.5 Symbol reassignment (FB → META)

`ref.identifier_aliases` is SCD-2. A ticker change:
1. `ref.identifier_aliases`: existing (`ticker`,`META`,US)→(entity_facebook) gets `valid_to = 2022-06-08`
2. New row: (`ticker`,`META`,US)→(entity_facebook) with `valid_from = 2022-06-09`
3. Old ticker gets a `ref.corporate_actions` row `action_type='ticker_change'`
4. Existing `listing_id` is unchanged; `ref.listings.trading_symbol` becomes `META` (SCD-2 on that column would be better; open question)

### 10.6 Delisting and survivorship bias

- Delisted listings get `ref.listings.active = false`, `last_traded = <date>`.
- `ref.universe_membership` records the delisting via `effective_to` and
  `reason='delisting'`.
- Backtests reading historical universe include delisted names — no survivorship bias.

### 10.7 Universe reconstruction

To reconstruct R3K as of 2024-03-15:
```sql
SELECT listing_id
FROM ref.universe_membership
WHERE universe_id = 'r3k'
  AND effective_from <= '2024-03-15'
  AND (effective_to IS NULL OR effective_to > '2024-03-15');
```
1 query, 1 table. No joins.

### 10.8 FX conversion at scale

Cross-country research (INR-listed vs USD-listed) needs USD-normalized
returns. Options:
1. Compute USD notional per bar on read (join to `market.fx_rates`)
2. Add `notional_usd` MV over `market.bars × market.fx_rates` (chosen)

MV convention: FX at `bar_time` close, `fix_convention='close'`. For research
that needs a different fix (WM/Reuters 4pm London), parameterize by rebuilding
MV with different fix_convention.

### 10.9 Entity resolution failure policy

Alt-data ingesters try to resolve `ticker` → `listing_id` via
`ref.identifier_aliases`. Two failure modes:
- **Not found**: row lands with `listing_id = NULL`, `resolution_confidence =
  'unresolved'`. Goes to a review queue (`ops.unresolved_entities`).
- **Ambiguous**: multiple candidate listings. Row lands with best guess,
  `resolution_confidence = 'medium'` or `'low'`. Reviewer promotes to
  `'manual_override'`.

Never silently drop rows. Never guess without recording confidence.

### 10.10 Restatement handling

Fundamentals get restated. Q1 2024 revenue announced 2024-04-15 as $X; amended
2025-02-10 as $Y.

Handled by `fundamentals.line_items` version bumps: each restatement is a new
row with same (`entity_id`, `tag`, `period_end`) but new `filing_id`, new
`filed_at`, new `as_of_time`. Original row is not overwritten.

Research PIT snapshot MV uses `argMax(value, filed_at) FILTER (filed_at <=
<backtest_date>)` → automatically gets the view that was current on that date.

---

## 11. Migration waves

Zero big-bang. Every wave runs old and new in parallel; cutover per wave.

**Wave 1 — Reference rebuild (2 weeks)**
1. Stand up `ref` database + all dimension tables
2. Bootstrap: LEI-GLEIF, OpenFIGI, ISO-10383 MICs, GICS
3. Backfill from `factorlab.ref_countries|ref_exchanges|ref_instruments|ref_contracts`
4. Build identifier resolver service; verify on 100 known cases
5. Keep old `ref_*` tables online

**Wave 2 — Market rebuild (2 weeks)**
1. Create `market.bars`, `market.bars_adjusted`, `market.fx_rates`
2. Backfill from `market_candles_1min`, `market_candles_daily`
3. Rewire ingesters: Upstox writes `market.bars`; Schwab same; EODHD same
4. Cutover research reads to `market.bars_adjusted`
5. Drop `market_candles_*`

**Wave 3 — Ops unification (1 week)**
1. Merge `us_*`, `india_*` operational tables → `ops.*`
2. Cutover dashboards / API
3. Drop old tables

**Wave 4 — Alt data generalization (2 weeks)**
1. Rename `alt_political_*` → `alt.political_*` under new schema
2. Backfill `listing_id`/`entity_id` via resolver
3. Create `legislator_trades_dedup` view (currently missing in prod — see fault F1)

**Wave 5 — Fundamentals (3-4 weeks)**
1. `fundamentals.filings`, `fundamentals.line_items`
2. Backfill from EODHD Fundamentals ($60/mo) + SEC EDGAR XBRL
3. Build `snapshots_pit` MV for top 200 tags

**Wave 6 — Derived + backtests (2 weeks)**
1. `derived.factors`, `derived.signals`, `derived.computations`
2. Migrate existing factor calc code
3. Bitemporal enforcement in research views

**Total: ~12-14 weeks focused work.**

---

## 12. What this doc explicitly does NOT cover

- Compute layer (Python/Polars/DuckDB research environment)
- API layer redesign (FastAPI endpoint restructuring)
- Frontend / Data Hub
- Deployment (Docker Compose, Cloudflare tunnel, etc.)
- Alt data ingestion for satellite, card panel, arxiv (schemas reserved, no ingest plan)
- L2 order book (deferred)
- Cryptocurrency, commodities, FX beyond conversion (out of scope)
- Latency SLOs and health monitoring (belongs to separate ops/health module)
- Continuous futures roll methodology (deferred alongside `derived.*`; table
  shape defined but methodology conventions TBD)

---

## 13. Deferred concerns with rationale

Items considered during review and deliberately deferred. Not gaps in
correctness — deferrals to earn scope.

| Item | Deferral | Rationale |
|---|---|---|
| **F2** — Data-quality-aware multi-vendor conflict resolution | Wave 2+ | Priority-based `bars_best` MV is fine for raw ingest. Cleaned/processed dataframes are downstream — quality-aware selection lives there, not in the raw curation layer. |
| **F3** — Sophisticated FX time-of-day handling (WM/Reuters 4pm etc.) | Post-launch | IBKR EOD snapshots for exposure currencies are sufficient for USD normalization at first. Multi-fix support added when specific research demands it. |
| **F11** — Continuous futures roll methodology (volume/OI/first-notice/proportional/gap) | With `derived.*` | Schema (`market.futures_continuous`) supports multiple methods; conventions defined when derived layer builds. |
| **F13** — Alt-data resolver SLA and review process | Post Wave 4 | Operational concern; put in place once alt data volume justifies it. |
| **F15** — Latency SLOs and pager/alert policy | Health module | Separate cross-cutting ops module owns this; schema-agnostic. |

---

## 14. Revision changelog

### Revision 2 — 2026-09-19

Post-review changes based on prop-shop-lens vetting.

**F1 — Enforced PIT** (section 10.4)
Was: "should include `WHERE as_of_time <=` clause."
Now: four-defense enforcement — role-based grants, parametric `research.*`
view schema, CI grep rule failing PRs that bypass, PR review template
mandating PIT checklist. Research role physically cannot query raw fact
tables.

**F4 — Listing migrations** (new `ref.listing_migrations`)
Handles cases where a security moves venues (Alibaba NYSE→HKEX ADR) that SCD-2
on `ref.identifier_aliases` alone cannot handle. Research view stitches
migration chain for continuous-return series.

**F5 — Restatement chain queryability** (`fundamentals.filings`)
Added `original_filing_id` (chain root, materialized at ingest for O(1) queries)
and `amendment_seq` alongside existing `amends_filing_id`. Restatement chain
traversal no longer requires recursive CTE.

**F6 — Trim `market.bars` sort key**
From 8 columns to 6. `session` and `source_channel` moved from sort key to
skip indices (they filter often but are low-cardinality). Keeps primary
index small and insert throughput high.

**F7 — `raw.archive` scan patterns**
Added `country_code` (nullable, for per-country retention) and bloom-filter
skip indices on `request_key` and `response_sha256`. Partition scheme extended
to `(source, country_code, YYYYMM)`. Retention now driven by cron-scheduled
DROP PARTITION with per-source-per-country policy.

**F8 — `derived.factors` sort key**
Moved `event_date` before `listing_id` so cross-sectional queries (dominant
pattern) prune tightly. Bloom index on `listing_id` preserves per-listing
lookup speed.

**F9 — `ops.lineage` retention**
TTL 90d for `market.*` rows (~99% of volume, rarely queried past a month),
2y for `alt.*`, permanent for `fundamentals.*` + `derived.*` where volume is
low and reproducibility matters. Alternative noted: fall back to run-level
lineage via `ops.ingestion_runs.listing_ids_touched` for market bars.

**F10 — `snapshots_pit` (invalid SQL)** (section 5)
Original spec used `argMax ... FILTER (WHERE)` which is not ClickHouse
syntax. Replaced with two-tier design:
- `fundamentals.snapshots_pit_eom` — physical wide table populated monthly
  by scheduled job. Fast for monthly-cadence backtests.
- `research.fundamentals_pit` — on-the-fly parametric view using `argMaxIf`
  for arbitrary `asof_date`. Slower but PIT-correct.

**F12 — Holiday-aware sessions** (section 3, extended)
Added `ref.holidays` override columns (`override_open/close/pre_close/post_open`).
New `ref.session_windows_effective` view resolves `(exchange, trade_date) →
(open, close, expected_bars_1min)` composing base sessions with holiday
overrides. Coverage checks now compute `expected` from this view — early-close
days no longer register as 66% coverage. Required data at launch: XNYS/XNAS
early closes (day-after-Thanksgiving, Christmas Eve, day-before-Independence),
XNSE holidays + muhurat trading (~1hr Diwali evening session).

**F14 — `ref.dataset_versions`** (new)
Tracks material rewrites of curated datasets (adjustment methodology,
tag-mapping revisions, universe reconstruction rules). Backtest reports pin
to `(dataset, version_tag)` tuples for reproducibility.

### Revision 1 — 2026-09-19
Initial full architecture draft.
