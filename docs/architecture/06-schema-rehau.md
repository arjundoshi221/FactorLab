# Schema Rehau — FactorLab v2

> Status: `[design]` — revision 11 (full findings resolution — F1-F22 addressed, §3 DDL shorthand normalized, info findings noted)
> Last verified: 2026-09-20

**Revision 11 changes** (2026-09-20): full resolution pass on the rev-10 punch
list in §19. Every finding F1 through F22 is addressed (some as DDL changes,
some as prose additions, one as a wave-blocker gate); the three info findings
(F23-F25) are reviewed and left as-is with a note. §3 (`ref`) DDL blocks are
swept for the systemic "shorthand vs full-syntax" gap flagged in §19.1 —
every table now uses full `CREATE TABLE (...)`, trailing commas, explicit
`ENGINE`, explicit `ORDER BY`, and the standard audit columns. A resolution
log is added as §19.8. Load-bearing schema changes in this revision:
`ref.entities.entity_type` declared (F1), `ref.adjustment_factors` gains PIT
columns + engine (F6), `ref.securities.sector_gics_id` → `sector_id` +
`sector_classification` (F9), `broker.account_state_snapshot.metric_canonical`
+ new `ref.broker_metrics_map` dim (F10), `broker.applied_corporate_actions`
gets a `security_id` bloom filter (F11), `broker.cash_flows.trade_name_id`
added for commission/fx rows (F8), `alt.political_trades.amount_currency`
added (F16), `book.trade_legs.side` → `leg_side` (F22), duplicate
`alt.social_reddit_posts` DDL block removed (F19). §13 renumbered:
`§13.13 Canonical FK conventions` → `§13.11`; new `§13.12` "Denormalization
rule: IDs only, not attributes" added (F7). Wave 8 in §15 gets an explicit
`[blocker]` gate for `ref.funds` (F2). Full changelog entry in §18.

**Revision 10 changes** (2026-09-20): full holistic audit of the rev-9 doc after
9 revisions of piecemeal edits. Deliverable is a findings punch list in the new
**§19**, plus a small set of trivial mechanical fixes applied inline (all
logged in §19.6). No table renames, no namespace moves, no design-level changes
— design-level findings are documented in §19 for user decision, not silently
applied. Fixes landed inline: (a) removed the contradictory `notional_usd`
column from `market.bars` DDL (prose already said it's an MV, not a stored
column); (b) fixed stale cross-reference `10.4.4` → `§13.4.4` in §14.7 (rev-8
renumber orphan); (c) added missing commas / `version, ingested_at` / ENGINE
clause on `ref.sessions`; (d) added missing ENGINE clause on `ref.holidays`.
Full changelog entry in §18.



*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 9 changes** (2026-09-20): namespace rename only. `ops` renamed to
`meta` throughout — the namespace stores freshness, coverage, lineage, drift,
and entity-resolution failures ("the data about our data"), which is data
observability, not trading operations. `meta` also matches the 3-4 char naming
rhythm of the rest of the schema (`ref`, `alt`, `raw`, `book`, `risk`). No
schema changes: same tables, same columns, same engines, same sort keys — only
the schema qualifier changes. Every live cross-reference updated (§2 namespace
table; §11.7 broker recon contract; §13.9 entity-resolution failure policy;
§14 test discipline; §15 migration waves; PIT time-zone list in §13.2).
Historical changelog entries in §18 (rev 8 and earlier) preserve their
as-written `ops.*` mentions as history.

*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 8 changes** (2026-09-20): pure numbering + audit refactor, no semantic
model changes to existing tables (aside from six additive columns called out
below). Two things happened:

1. **Section renumber for logical flow.** Rev 7 dropped `book` and `risk` in
   as suffix sections `§7A` and `§7B` (a shortcut). Rev 8 promotes them to
   proper numbered sections and physically moves them ahead of `derived` so
   the doc reads intent → compute → ops → broker → raw → cross-cutting →
   testing → migration → NOT-covered → deferred → changelog. Old → new:
   `§7A`→`§7 book`, `§7B`→`§8 risk`, `§7 derived`→`§9`, `§8 ops`→`§10`,
   `§9 broker`→`§11`, `§10 raw`→`§12`, `§11 cross-cutting`→`§13`,
   `§12 testing`→`§14`, `§13 migration waves`→`§15`, `§14 NOT covered`→`§16`,
   `§15 deferred concerns`→`§17` (`§15.1`→`§17.1`), `§16 changelog`→`§18`.
   Subsection numbers shift with parent: `§9.1-§9.13` (broker) →
   `§11.1-§11.13`; `§11.1-§11.13` (cross-cutting) → `§13.1-§13.11`
   (originally noted as `§13.1-§13.13`; two subsections were consolidated
   in rev 8 without noting the tail — rev 11 (F5) fixes the numbering);
   `§11.4.1-§11.4.5` (grant / research / CI / PR-template / not-enforced,
   which were mis-numbered as `10.4.x` under rev 7) become `§13.4.1-§13.4.5`;
   `§12.1-§12.8` (testing) → `§14.1-§14.8`. Every live in-body cross-reference
   updated; rev-7 changelog and older rev summaries retain their as-written
   numbers as historical record.
2. **Fund-structure connectivity audit — new §7.7.** Walked the `book.*` +
   `risk.budgets` + `broker.*` + `derived.forward_test_attribution` graph
   end-to-end for FK integrity, denorm consistency, PIT columns, sort-key
   appropriateness, and PM-usability. Six additive columns fell out (all
   nullable, all documented in §7.7):
   - `book.trade_names.base_currency FixedString(3)` — multi-currency book support
   - `book.trade_names.priority Enum8('high','medium','low','watch')` — conviction rating
   - `book.trade_names.owner_entity_id Nullable(UUID)` — who runs the trade_name
   - `book.themes.owner_entity_id Nullable(UUID)` — theme sponsor / responsible PM
   - `book.attribution.computation_id Nullable(UUID)` — lineage for rule-based assignments
   - `book.trade_legs.entered_at_price_avg Nullable(Decimal(20,6))` — realized entry basis at leg level
   Also renamed `book.trade_names.opened_by_entity_id` to co-exist with the new
   `owner_entity_id` (opener ≠ current owner). Sort key on `book.attribution`
   already leads with `trade_name_id` — no fix needed (the primary query
   surface). `broker.cash_flows` explicitly left without `trade_name_id`
   (rationale in §7.7). No FK types mismatched.



Full redesign of the FactorLab ClickHouse footprint. Replaces the flat 19-table
`factorlab` database. Targets: (1) every research question ≤ 1–2 joins away,
(2) point-in-time correctness enforced by grants and CI, not convention,
(3) horizontal scale to G10 + APAC without new tables, (4) hedge-fund-grade
provenance and lineage, (5) canonical FactorLab UUIDs across every fact.

This doc is the authoritative spec for what to build. `docs/architecture/02-database-clickhouse.md`
becomes the *why*; this doc is the *what*.

*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 7 changes** (2026-09-20): supersedes rev 6 same-day. The rev-6 fund-
structure additions were placed in `derived.*` — wrong home for human-authored
investment intent (the `derived` namespace is *reproducible computed data*).
Rev 7 splits them into two new namespaces that mirror how multi-manager hedge
funds actually organize (Millennium / Citadel / Balyasny vocabulary): **`book.*`
for PM-owned intent** (strategies, themes, trade_names, trade_legs,
attribution) and **`risk.*` for risk-owned governance** (budgets today; limits,
exposure snapshots, VaR, drawdown events tomorrow). `derived.strategies` moved
to `book.strategies`. The prior `derived.trades` is renamed to
`book.trade_names` (the word `trade` alone is banned as an entity — always
`trade_name`). `derived.themes`, `derived.trade_legs`, and
`derived.position_attribution` move to `book.themes`, `book.trade_legs`,
`book.attribution` (renamed) — all with `trade_id` → `trade_name_id`
throughout. `derived.risk_budgets` moves to `risk.budgets` with `scope` enum
values `'fund','strategy','theme','trade_name'`. Broker extensions and rollup
MVs updated: `trade_id` → `trade_name_id`, `exposure_by_trade_daily` →
`exposure_by_trade_name_daily`, `pnl_by_trade_daily` →
`pnl_by_trade_name_daily`. Canonical FK UUIDs are now `strategy_id`,
`theme_id`, `trade_name_id`. `derived.forward_test_attribution` stays in
`derived` (it IS computed) but its `strategy_id` now points to
`book.strategies`. Wave 8, §11.13, §12.1, §15.1, §16 all updated. See rev-6
note below — retained but superseded.

*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 6 changes** (2026-09-20) — **SUPERSEDED BY REV 7 SAME-DAY, SEE
ABOVE.** Original text: fund-structure layer added between `derived.strategies`
and `broker.positions_snapshot`. Introduced Fund → Strategy → Theme → Trade →
Position leg hierarchy under `derived.*`. The tables themselves survive in
rev 7; only their namespace and the `trade` → `trade_name` naming changed.
Placement in `derived.*` was rejected because strategies/themes/trades are
human-authored intent, not reproducible computation.

*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 5 changes** (2026-09-20): schema completeness pass for the
broker/portfolio pipeline. Added `ref.broker_accounts` (account dim),
`ref.execution_methods` (origin-of-order dim), `derived.strategies` (strategy
registry) and `derived.forward_test_attribution` (paper-vs-backtest scoring),
`ops.reconciliation_drift` and `ops.unresolved_entities` (schemas for
previously-referenced tables). Extended `broker.positions_snapshot` with FX-PIT
+ per-position margin. Extended `broker.executions` with `execution_method_id`,
`order_ref`, `route_pref`, and `strategy_id`. Added `strategy_id` to
`broker.open_orders_snapshot`. New tables `broker.cash_flows`
(dividends/interest/deposits — otherwise NAV drift is unexplained),
`broker.applied_corporate_actions`, and `broker.order_events` (full order
lifecycle — enables slippage decomposition, latency profiling, execution-quality
analysis). New MV `broker.margin_state_daily`.

*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 4 changes** (2026-09-19): added `broker.*` namespace (new section 9)
for portfolio and execution monitoring — mirrors broker-side truth (positions,
account state, executions, open orders) across paper and live accounts,
time-series by design. New Wave 7 in migration plan. Sections 10–16 renumbered
from prior 9–15.

*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 3 changes** (2026-09-19): fully absorbed political-data audit —
`ref.legislator_terms` added, `alt.*` section rewritten with canonical FK
resolution, SCD-2 committee memberships, resolution-confidence enums. New
section 12 (testing + code review discipline) with 11 test categories, coverage
targets, and bug-class-to-test traceability. New section 11.13 (canonical FK
conventions) formalizing the four FactorLab UUIDs + person canonical. Wave 4
expanded to 4 tiers with explicit exit criteria. See section 16 for full history.

*(Numbering below is as-written under this revision — see §18 for current section numbers.)*

**Revision 2 changes** (2026-09-19): incorporated review findings F1, F4, F5,
F6, F7, F8, F9, F10, F12, F14. Deferred: F2 (data-quality-aware conflict
resolution — will address in post-ingest processing), F3 (FX complexity —
using IBKR EOD snaps for exposure currencies), F11 (futures roll methodology
— deferred with `derived.*`), F13 (alt-data SLA), F15 (latency SLOs —
separate ops/health module).

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

Rows are ordered by build-wave dependency (dims first, then facts, then intent,
then compute, then broker, then observability, then archive) so a reader walking
the table sees the same order the migration waves in §15 follow.

| Database | Purpose | Change velocity | Retention |
|---|---|---|---|
| `ref` | Dimensions: countries, exchanges, securities, listings, entities, aliases, universes | Slow (SCD-2) | Forever |
| `market` | Prices, quotes, corporate actions, FX | Append-heavy | Bars forever; quotes 90d |
| `fundamentals` | Filings, line items, snapshots, holdings, insider transactions | Batch-daily | Forever |
| `alt` | Political, social, research, satellite, card panel | Batch to real-time | Per source |
| `book` | PM-authored investment intent: strategies, themes, trade_names, trade_legs, execution→trade attribution. Owned by the PM; the "book" is standard hedge-fund vocabulary. | Human-authored, edited on trade lifecycle events | Forever (append-only via SCD-2) |
| `risk` | Risk-owned governance constraints: budgets today; limits, exposure snapshots, VaR, drawdown events on the roadmap. Owned by risk, not by PM. | SCD-2 versioned; edits are governance events | Forever |
| `derived` | Reproducible computed data: factors, signals, portfolios, backtests, forward-test attribution | Recomputable | Recompute on demand |
| `broker` | Portfolio & execution monitoring: positions, account state, executions, open orders (paper + live) | Snapshotted 2× daily + intraday for execs | Forever |
| `meta` | Data observability: pipeline health (ingestion runs, coverage, source status, expected series), lineage, reconciliation drift, unresolved-entity queue — the data about our data | Continuous | 2y hot, 5y cold |
| `raw` | Raw HTTP + stream payload archive | Append-only, immutable | Per-source retention |

---

## 3. `ref` — Dimensions in 3NF

### `ref.countries`

```sql
CREATE TABLE ref.countries (
    country_code       FixedString(2),           -- ISO-3166 alpha-2, single source of truth
    name               String,
    region             LowCardinality(String),   -- 'americas','emea','apac'
    default_currency   FixedString(3),           -- ISO-4217 alpha-3
    timezone           String,                   -- IANA
    active             Bool,
    first_active_at    Date,
    version            UInt64,
    ingested_at        DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY country_code;
```

**Change from today:** `market_code` is removed. It was redundant with
`country_code`. One ISO-3166 code everywhere.

### `ref.currencies`

```sql
CREATE TABLE ref.currencies (
    currency_code      FixedString(3),           -- ISO-4217
    name               String,
    is_deliverable     Bool,                     -- 'INR' is non-deliverable offshore
    version            UInt64,
    ingested_at        DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY currency_code;
```

### `ref.exchanges`

```sql
CREATE TABLE ref.exchanges (
    exchange_code      LowCardinality(String),   -- 'NASDAQ','NYSE','NSE','LSE','TSE','HKEX','SGX'
    mic                FixedString(4),           -- ISO-10383 (XNAS, XNYS, XNSE)
    name               String,
    country_code       FixedString(2),           -- FK ref.countries
    currency_code      FixedString(3),           -- FK ref.currencies (native trading ccy)
    timezone           String,
    sessions           String,                   -- JSON: pre/regular/post windows
    active             Bool,
    version            UInt64,
    ingested_at        DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY exchange_code;
```

### `ref.sectors`

```sql
CREATE TABLE ref.sectors (
    sector_id          String,                   -- '10','1010','101010','10101010' (GICS 2/4/6/8)
    level              UInt8,                    -- 2,4,6,8
    parent_id          Nullable(String),
    name               String,
    classification     LowCardinality(String),   -- 'gics','icb','naics','trbc'
    active             Bool,
    version            UInt64,
    ingested_at        DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (classification, sector_id);
```

Same table serves GICS, ICB, NAICS, TRBC — classification dimension
distinguishes. The `classification` enum is the authoritative list referenced
by `ref.securities.sector_classification` (see F9 rev 11).

### `ref.sources`

```sql
CREATE TABLE ref.sources (
    source_id          LowCardinality(String),   -- 'schwab','upstox','eodhd','ibkr','sec_edgar',
                                                 --   'house_clerk_ptr','fec','reddit','arxiv'
    kind               LowCardinality(String),   -- 'market','fundamentals','political','social','research'
    vendor             LowCardinality(String),
    auth_type          LowCardinality(String),   -- 'oauth2','api_key','none','scraping'
    countries          Array(FixedString(2)),    -- ['US'], ['IN'], ['US','GB','JP',...] or ['*'] for global
    active             Bool,
    notes              String,
    version            UInt64,
    ingested_at        DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY source_id;
```

Every `source` in every fact/ops row FKs here.

### `ref.execution_methods`

**Why this exists**: `broker.executions` needs to answer *"where did this fill originate — a specific API service, a manual GUI order, mobile, an algo?"* IBKR only exposes `Execution.clientId` (the API clientId that placed it, or 0 for manual). This dim table maps clientId → a human-meaningful method label so downstream slippage/attribution queries can group by origin without hardcoding numeric IDs.

```sql
CREATE TABLE ref.execution_methods (
    method_id           LowCardinality(String),   -- 'api_trade_engine_v1','api_research_notebook',
                                                   --   'manual_gui_or_mobile','manual_tagged_mobile',
                                                   --   'algo_vwap','algo_adaptive'
    category            LowCardinality(String),   -- 'api' | 'manual' | 'algo'
    api_client_id       Nullable(Int32),          -- populated for category='api'; NULL for manual/algo
    algo_strategy       Nullable(String),         -- populated for category='algo' (matches Order.algoStrategy)
    description         String,
    active              Bool,
    valid_from          Date,
    valid_to            Nullable(Date),           -- SCD-2 (clientId reassigned or service retired)
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (method_id, valid_from);
```

**Resolution flow at ingest:**
1. If `Execution.clientId != 0` → look up by `api_client_id` → method_id (category='api')
2. Else if `Order.algoStrategy` set → look up by `algo_strategy` → method_id (category='algo')
3. Else if `Order.orderRef` matches known pattern (e.g. tagged 'mobile') → tagged method
4. Else → `manual_gui_or_mobile` (unavoidable ambiguity — IBKR does not distinguish desktop/mobile/web at fill time)

### `ref.broker_metrics_map`

**Why this exists** (added rev 11 per F10): `broker.account_state_snapshot`
carries `metric` values verbatim from the vendor — IBKR emits `'NetLiquidation'`,
`'BuyingPower'`, `'MaintMarginReq'`; Schwab uses different names for the same
concepts. Without a canonical mapping, every downstream MV (`broker.nav_daily`,
`broker.margin_state_daily`) hardcodes vendor-specific metric names and breaks
the day a second broker lands. This dim maps `(broker_code, vendor_metric)` →
`canonical_metric` so cross-broker rollups work uniformly.

```sql
CREATE TABLE ref.broker_metrics_map (
    broker_code           LowCardinality(String),   -- 'ibkr','schwab',...
    vendor_metric         LowCardinality(String),   -- vendor-native tag: 'NetLiquidation','BuyingPower',...
    canonical_metric      LowCardinality(String),   -- factorlab-canonical:
                                                    --   'net_liquidation','buying_power','cash','equity',
                                                    --   'maint_margin_req','init_margin_req',
                                                    --   'excess_liquidity','cushion','leverage','sma',...
    description           String,
    active                Bool,
    version               UInt64,
    ingested_at           DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (broker_code, vendor_metric);
```

Seeded from the ~144 IBKR account tags (see [`docs/data-sources/06-ibkr.md`]) at
onboarding; extended per broker as new integrations land. The map is closed:
a vendor metric with no canonical target lands in `meta.unresolved_entities`
for review, not passed through as-is.

### `ref.entities`

The **issuer / company / person / committee / organization** layer. LEI-keyed
where the entity is a legal person; other identity kinds keyed by canonical
FactorLab UUID.

```sql
CREATE TABLE ref.entities (
    entity_id             UUID,                     -- factorlab canonical
    entity_type           LowCardinality(String),   -- discriminator (F1 rev 11) — 'issuer',
                                                     --   'person_legislator','person_pm',
                                                     --   'person_analyst','person_committee_member',
                                                     --   'committee','organization',
                                                     --   'government_body','other'
    legal_name            String,
    lei                   Nullable(FixedString(20)),  -- ISO-17442
    country_of_domicile   FixedString(2),
    country_of_incorp     FixedString(2),
    active                Bool,
    first_seen            Date,
    last_seen             Date,
    version               UInt64,
    ingested_at           DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY entity_id;
```

`entity_type` is the discriminator that makes the "same UUID space, different
type" rule in §13.11 work — every consumer that hits `ref.entities` filters on
it (`alt.political_committees.committee_entity_id` on `entity_type='committee'`,
`ref.legislator_terms.legislator_entity_id` on `entity_type='person_legislator'`,
`book.trade_names.owner_entity_id` on `entity_type='person_pm'`, and so on).
The enum is closed — a new value requires a schema migration and a policy
review (does a new entity type need its own dim table instead?).

Corporate actions that change the entity (M&A, spinoffs) create new
entity rows with cross-references in `ref.entity_relationships`.

### `ref.entity_relationships`

```sql
CREATE TABLE ref.entity_relationships (
    parent_entity_id      UUID,
    child_entity_id       UUID,
    relationship          LowCardinality(String),   -- 'ma','spinoff','rename','subsidiary'
    effective_date        Date,
    end_date              Nullable(Date),
    notes                 String,
    version               UInt64,
    ingested_at           DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (parent_entity_id, effective_date);
```

### `ref.securities`

The **claim / instrument** layer. ISIN-keyed.

```sql
CREATE TABLE ref.securities (
    security_id            UUID,                        -- factorlab canonical
    entity_id              UUID,                        -- FK ref.entities (entity_type='issuer')
    security_type          LowCardinality(String),      -- 'common','preferred','adr','etf','etn',
                                                        --   'reit','warrant','right','mutual_fund',
                                                        --   'index','future','option','bond','fx_pair'
    isin                   Nullable(FixedString(12)),
    cusip                  Nullable(FixedString(9)),
    figi                   Nullable(FixedString(12)),
    share_class            LowCardinality(String),      -- 'A','B','C','' (single class)
    currency_code          FixedString(3),              -- FK ref.currencies (denomination)
    issue_date             Nullable(Date),
    maturity_date          Nullable(Date),              -- bonds, options, futures
    sector_id              Nullable(String),            -- FK ref.sectors — renamed rev 11 (F9)
    sector_classification  LowCardinality(String),      -- 'gics','icb','naics','trbc' — added rev 11 (F9);
                                                        --   pairs with sector_id, matches ref.sectors.classification
    active                 Bool,
    version                UInt64,
    ingested_at            DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY security_id;
```

**Multiple classifications over history**: a security may have GICS today and
migrate to ICB tomorrow, or an emerging-market security may only have NAICS.
The rename from `sector_gics_id` to `sector_id` + `sector_classification`
(rev 11, F9) makes the classification system a first-class column. When
classification changes for the same security, a new SCD-2-style row lands
under `ReplacingMergeTree(version)` — history is preserved by not rewriting
the older row's `ingested_at`. Cross-classification queries filter on
`sector_classification` explicitly; naive queries that ignore it will mix GICS
and ICB codes as if they were the same enum (they are not) — a class of bug
the paired column defends against.

### `ref.listings`

The **venue-scoped tradable**. This is the primary `factorlab_id` for market data.

```sql
CREATE TABLE ref.listings (
    listing_id            UUID,                         -- ★ factorlab canonical for market data ★
    security_id           UUID,                         -- FK ref.securities
    exchange_code         LowCardinality(String),       -- FK ref.exchanges
    country_code          FixedString(2),               -- denormalized from exchange
    trading_symbol        LowCardinality(String),       -- 'AAPL', 'TCS', '7203'
    local_symbol          Nullable(String),             -- exchange-native symbol if different
    mic                   FixedString(4),               -- ISO-10383
    lot_size              UInt32,
    tick_size             Nullable(Decimal(18,6)),
    is_primary            Bool,                         -- primary listing for the security
    first_traded          Nullable(Date),
    last_traded           Nullable(Date),               -- delisting date
    active                Bool,
    version               UInt64,
    ingested_at           DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY listing_id;
```

### `ref.contracts`

The **derivative contract** layer. Options + futures.

```sql
CREATE TABLE ref.contracts (
    contract_id           UUID,                         -- factorlab canonical for the derivative
    underlying_listing_id UUID,                         -- FK ref.listings (underlying)
    exchange_code         LowCardinality(String),
    country_code          FixedString(2),
    contract_type         LowCardinality(String),       -- 'future','call','put'
    expiry                Date,
    strike                Nullable(Decimal(18,6)),      -- options only
    right                 LowCardinality(String),       -- 'C','P','' (futures)
    exercise_style        LowCardinality(String),       -- 'american','european',''
    multiplier            UInt32,                       -- shares per contract
    lot_size              UInt32,
    tick_size             Nullable(Decimal(18,6)),
    weekly                Bool,
    active                Bool,
    version               UInt64,
    ingested_at           DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (underlying_listing_id, expiry, right, strike);
```

### `ref.broker_accounts`

**Why this exists**: Every row in `broker.*` carries `account_id`, `account_mode`, `country_code`, and implicitly a `base_currency`. Without this dim table, that metadata is hardcoded in ingesters (paper `DUE375963` = US-booked USD, live `U18065781` = SG-booked SGD, etc.) and every downstream query has to re-derive account context. This makes "add a new account" a code change instead of a row insert, and any cross-account rollup ("total USD-equivalent NAV across all live accounts") requires manual FX plumbing.

```sql
CREATE TABLE ref.broker_accounts (
    account_id          String,                   -- IBKR-native: 'DUE375963','U18065781'
    broker_code         LowCardinality(String),   -- 'ibkr' (later: 'schwab','upstox',...)
    account_mode        LowCardinality(String),   -- 'paper' | 'live'
    base_currency       FixedString(3),           -- 'USD','SGD','INR' — account's booking currency
    booking_country     FixedString(2),           -- 'US','SG','IN'
    account_type        LowCardinality(String),   -- 'individual','joint','ira','trust','corp'
    margin_type         LowCardinality(String),   -- 'cash','margin','portfolio_margin'
    opened_date         Date,
    closed_date         Nullable(Date),
    purpose             LowCardinality(String),   -- 'forward_test','production','client_mandate_<code>'
    strategy_ids        Array(UUID),              -- FK book.strategies — CANONICAL (F17 rev 11):
                                                   --   this is the source of truth for "which strategies
                                                   --   run on this account". book.strategies.paper_account_ids
                                                   --   / live_account_ids are the denormalized inverse
                                                   --   maintained for query speed; nightly integrity check
                                                   --   (§14 test category) asserts the two agree.
    notes               String,
    active              Bool,
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (broker_code, account_id);
```

Seeded once at onboarding from `ib.managedAccounts()` plus a one-time manual fill for `base_currency`, `booking_country`, `account_type`, `margin_type` (IBKR API does not expose all of these directly). Updates via SCD-2-style row rewrites are rare — account metadata is slow-moving.

### `ref.identifier_aliases`

The universal resolver. Any vendor ID → canonical UUID.

```sql
CREATE TABLE ref.identifier_aliases (
    alias_kind            LowCardinality(String),           -- 'ticker','isin','cusip','figi','lei',
                                                            --   'sedol','openfigi','bloomberg',
                                                            --   'schwab_conid','ibkr_conid','eodhd_symbol',
                                                            --   'upstox_instrument_key','fec_candidate_id',
                                                            --   'bioguide','cik','ric','permid'
    alias_value           String,
    scope_country         Nullable(FixedString(2)),         -- ticker namespaces are country-scoped
    scope_exchange        Nullable(LowCardinality(String)), -- for MIC-scoped tickers
    target_kind           LowCardinality(String),           -- 'entity','security','listing','contract'
    target_id             UUID,
    source                LowCardinality(String),           -- who told us this
    valid_from            Date,
    valid_to              Nullable(Date),                   -- SCD-2 (ticker reassignment)
    confidence            Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'manual_override'=5),
    notes                 String,
    version               UInt64,
    ingested_at           DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (alias_kind, alias_value, valid_from);
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
CREATE TABLE ref.corporate_actions (
    action_id             UUID,
    security_id           UUID,                     -- FK ref.securities
    listing_id            Nullable(UUID),           -- if action is listing-scoped
    action_type           LowCardinality(String),   -- 'split','dividend','spinoff','rights',
                                                    --   'delisting','ticker_change','merger'
    announcement_date     Nullable(Date),
    ex_date               Date,                     -- primary sort key
    record_date           Nullable(Date),
    payable_date          Nullable(Date),
    effective_date        Date,
    split_from            Nullable(UInt32),         -- 2 in a 2:1 split
    split_to              Nullable(UInt32),         -- 1 in a 2:1 split
    cash_amount           Nullable(Decimal(18,6)),  -- dividend / cash-in-lieu
    currency_code         Nullable(FixedString(3)),
    old_symbol            Nullable(String),         -- ticker change
    new_symbol            Nullable(String),
    notes                 String,
    source                LowCardinality(String),
    raw_id                Nullable(UUID),
    as_of_time            DateTime64(3, 'UTC'),
    version               UInt64,
    ingested_at           DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (security_id, ex_date, action_id);
```

### `ref.adjustment_factors`

Materialized/computed daily-cumulative adjustment factors per listing.

```sql
CREATE TABLE ref.adjustment_factors (
    listing_id            UUID,
    event_date            Date,
    price_factor          Decimal(18,10),           -- multiply raw prices by this to get adjusted
    volume_factor         Decimal(18,10),           -- multiply raw volumes by this
    computation_id        UUID,                     -- lineage
    computed_at           DateTime64(3, 'UTC'),
    -- PIT columns (added rev 11 per F6) — nightly recompute must not silently overwrite history
    as_of_time            DateTime64(3, 'UTC'),
    ingested_at           DateTime64(3, 'UTC'),
    version               UInt64
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYear(event_date)                     -- low volume; year partitions keep part count small
ORDER BY (listing_id, event_date);
```

Nightly recompute of adjustment factors appends new rows with later
`as_of_time`; consumers of `market.bars_adjusted` pin to a `dataset_version`
from `ref.dataset_versions` for reproducibility. Without these columns
(the pre-rev-11 shape), an in-place overwrite of a corrected split factor
would silently rewrite every historical `bars_adjusted` result — a
reproducibility hole that turns a Wednesday backtest into a different number
on Thursday for reasons the researcher can't see.

### `ref.universes` and `ref.universe_membership`

```sql
-- Universe definitions
CREATE TABLE ref.universes (
    universe_id    LowCardinality(String),    -- 'r3k','r1k','sp500','nifty50','nifty500','custom_watchlist'
    name           String,
    provider       LowCardinality(String),    -- 'russell','msci','sp','nse','custom'
    rebalance_freq LowCardinality(String),    -- 'quarterly','annual','continuous'
    country_scope  Array(FixedString(2)),
    active         Bool,
    version        UInt64,
    ingested_at    DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY universe_id;

-- Point-in-time membership (SCD-2)
CREATE TABLE ref.universe_membership (
    universe_id    LowCardinality(String),
    listing_id     UUID,
    weight         Nullable(Float32),         -- for weighted indices
    effective_from Date,                       -- inclusive
    effective_to   Nullable(Date),             -- inclusive; NULL = current
    reason         LowCardinality(String),     -- 'reconstitution','ipo','delisting','merger'
    source         LowCardinality(String),
    raw_id         Nullable(UUID),
    version        UInt64,
    ingested_at    DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (universe_id, effective_from, listing_id);
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

### `ref.legislator_terms` (SCD-2)

Legislators are **entities** (`ref.entities` rows with `entity_type='person_legislator'`).
Their term-scoped attributes (chamber, state, district, party, seat class) change
across terms and live here, SCD-2. Former legislators keep their `entity_id`
forever, so historical trade attribution never breaks when a member retires or
changes chamber.

```sql
CREATE TABLE ref.legislator_terms (
    legislator_entity_id  UUID,                       -- FK ref.entities
    bioguide_id           String,                     -- persistent legislator ID (Bioguide)
    congress_number       UInt16,                     -- term Congress
    chamber               LowCardinality(String),     -- 'house','senate'
    state                 FixedString(2),
    district              Nullable(UInt16),           -- house only
    party                 LowCardinality(String),
    seat_class            Nullable(UInt8),            -- senate class 1/2/3
    term_start            Date,
    term_end              Date,                       -- known at term start (fixed length)
    in_office             Bool,                       -- current-term flag
    source                LowCardinality(String),
    raw_id                Nullable(UUID),
    as_of_time            DateTime64(3, 'UTC'),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (bioguide_id, term_start);
```

Point-in-time attribution: "which party was Sen. X in on 2019-03-15" =
`WHERE bioguide_id = ... AND term_start <= '2019-03-15' AND term_end > '2019-03-15'`.

### `ref.sessions` and `ref.holidays`

```sql
CREATE TABLE ref.sessions (
    exchange_code  LowCardinality(String),
    session        LowCardinality(String),      -- 'pre','regular','post','open_auction','close_auction'
    day_of_week    UInt8,                        -- 0-6
    starts_at      String,                       -- 'HH:MM' local
    ends_at        String,
    effective_from Date,
    effective_to   Nullable(Date),
    version, ingested_at                         -- (added rev 10 for consistency with sibling dims)
) ENGINE = ReplacingMergeTree(version)
ORDER BY (exchange_code, effective_from, session);

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
) ENGINE = ReplacingMergeTree(version)                 -- (added rev 10: was missing)
ORDER BY (country_code, holiday_date);

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

**Downstream consumers:** `meta.session_coverage.expected` must be computed
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
    -- notional_usd is NOT stored here — a raw notional_usd would be frozen at
    --   ingest-time FX. It is computed at read time by the market.bars_notional_usd MV
    --   (see §13.8). Removed from this DDL in rev 10 to resolve a contradiction
    --   with the prose below. (Fix F16 — trivial, rev 10.)
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
- Volume: shares for spot, contracts for futures. **Never sum across `product_type`** —
  use `market.volume_by_product_type_daily` (reserved MV name, built Wave 5+;
  partitions strictly by `product_type` so cross-summing shares and contracts
  is structurally impossible). Cross-summing raw `market.bars.volume` across
  `product_type` is a bug class (a single-stock-future contract counted as
  one share, a share counted as one contract) that has silently poisoned
  volume/OI dashboards in prior systems. The same warning applies to
  `market.options_bars.volume` — options volumes are contracts, never sum
  across `product_type` with spot bars.
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
PARTITION BY (toYYYYMM(expiry), toYYYYMM(bar_time))  -- TODO measure at Wave 5+ (F15 rev 11):
                                                     --   5y × 12 active expiries × 12 bar months ≈ 720
                                                     --   partitions plus weeklies; on the edge. If part
                                                     --   count grows too much, drop to
                                                     --   (toYear(expiry), toYYYYMM(bar_time)).
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
source + raw_id + resolved canonical FKs (`entity_id`/`security_id`/`listing_id`/`contract_id`)
+ payload. **No repeated slowly-changing attributes on facts.** Party, sector,
currency, exchange, chamber-per-term are FK-lookups to `ref.*`.

Design driven by the 2026-09-19 political-data audit (see
`docs/data-sources/political/quality-audit.md` when published):
- Legislator identity: was 74% resolved. Target 95%+ via deterministic normalizer.
- Amount parsing: was 99.6% defaulted (Sev-1). Fix: never default; NULL if unreadable.
- Asset type: was inferred from ticker regex (~10% mistags). Fix: trust filing metadata.
- Ticker → canonical listing: was ~1% resolved (empty ref). Target 85-95% post-Wave-1.
- Options: were tagged as underlying only, losing strike/expiry. Fix: OCC parse → `contract_id`.
- Committee memberships: were daily snapshots (105K rows in 6 weeks). Fix: SCD-2.
- Senate data: missing from ClickHouse (lives in Postgres). Fix: unify.

### `alt.political_trades`

```sql
CREATE TABLE alt.political_trades (
    -- identity --
    political_trade_id          UUID,                              -- factorlab canonical (renamed from trade_id in rev 7
                                                                    --   to avoid collision with book.trade_names.trade_name_id)
    country_code                FixedString(2),                    -- 'US' (extends when EU/UK filings ingested)
    chamber                     LowCardinality(String),            -- 'house','senate'

    -- filing linkage --
    filing_id                   String,                            -- source-native
    filing_url                  String,
    filing_date                 Date,
    transaction_date            Date,
    notification_date           Nullable(Date),

    -- LEGISLATOR (raw + resolved) --
    legislator_name_raw         String,                            -- provenance only
    bioguide_id                 Nullable(String),                  -- persistent Bioguide id
    legislator_entity_id        Nullable(UUID),                    -- FK ref.entities (person_legislator)
    bioguide_confidence         Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),

    -- ASSET (raw from filing) --
    ticker_raw                  Nullable(String),                  -- what filing said
    asset_name_raw              String,                            -- what filing said
    filing_asset_type_code      LowCardinality(String),            -- what filing said: 'ST','OP','MF','BD','CT','GS','HN','OT'

    -- ASSET (resolved to canonical) --
    security_type               LowCardinality(String),            -- factorlab-canonical: 'common','preferred','adr',
                                                                    -- 'etf','option','bond','muni','mutual_fund',
                                                                    -- 'crypto','partnership','warrant','treasury','other'
    listing_id                  Nullable(UUID),                    -- FK ref.listings (venue-scoped tradable)
    contract_id                 Nullable(UUID),                    -- FK ref.contracts (options only; OCC-parsed)
    security_id                 Nullable(UUID),                    -- FK ref.securities (security-level)
    entity_id                   Nullable(UUID),                    -- FK ref.entities (issuer)
    resolution_confidence       Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),

    -- TRANSACTION --
    transaction_type            LowCardinality(String),            -- 'purchase','sale_full','sale_partial','exchange'
    owner_code                  LowCardinality(String),            -- 'self','spouse','child','joint','dependent_child'
    filer_type                  LowCardinality(String),
    amount_bucket_id            LowCardinality(String),            -- '$1K-$15K','$15K-$50K',...,$50M+'
    amount_min                  Nullable(UInt64),                  -- NEVER default; NULL if unextractable
    amount_max                  Nullable(UInt64),
    amount_currency             FixedString(3) DEFAULT 'USD',      -- ISO-4217; added rev 11 per F16 —
                                                                    --   default 'USD' for US House/Senate PTRs;
                                                                    --   set explicitly when EU/UK filings ingest
    amount_str_raw              String,                            -- provenance

    -- provenance / audit --
    source                      LowCardinality(String),
    source_channel              LowCardinality(String),            -- 'house_clerk_ptr','senate_efd','ssw_backfill'
    parser_version              LowCardinality(String),
    raw_id                      Nullable(UUID),                    -- FK raw.archive
    ingest_run_id               UUID,                              -- FK meta.ingestion_runs
    as_of_time                  DateTime64(3, 'UTC'),
    ingested_at                 DateTime64(3, 'UTC'),
    version                     UInt64,

    INDEX idx_listing     listing_id     TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_entity      entity_id      TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_bioguide    bioguide_id    TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(transaction_date)
ORDER BY (country_code, chamber, transaction_date, entity_id, political_trade_id);
```

**5NF adherence:**
- Fact carries only FKs to canonical dims — no `party`, `state`, `district`,
  `sector`, `exchange`, `currency`, etc.
- Deliberate 5NF violations, all called out: `country_code` (partition prune),
  `chamber` (fact-scoped, not derivable from entity — it's who filed, not who traded).
- Raw provenance columns (`_raw` suffix) are provenance, not denormalization.

### `alt.political_filings`

Filing HEADERS. One row per filing. Amendments handled the same way as
`fundamentals.filings` — chain via `amends_filing_id` + `original_filing_id`.

```sql
CREATE TABLE alt.political_filings (
    filing_id                   String,                            -- source-native
    country_code                FixedString(2),
    chamber                     LowCardinality(String),
    filing_type                 LowCardinality(String),            -- 'P' (PTR), 'FD' (annual), 'A' (amendment)
    filing_year                 UInt16,
    filing_date                 Date,
    filer_name_raw              String,
    bioguide_id                 Nullable(String),
    legislator_entity_id        Nullable(UUID),                    -- FK ref.entities
    bioguide_confidence         Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    filing_url                  String,
    amends_filing_id            Nullable(String),                  -- immediate predecessor
    original_filing_id          Nullable(String),                  -- chain root (materialized at ingest)
    amendment_seq               UInt8 DEFAULT 0,
    is_amended                  Bool,
    is_amendment                Bool,
    trade_count                 UInt32,                            -- denorm from alt.political_trades count
    source, source_channel, raw_id, ingest_run_id,
    as_of_time, ingested_at, version
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (chamber, filing_year)
ORDER BY (country_code, chamber, filing_id);
```

### `alt.political_committee_memberships` (SCD-2)

Fixed from the daily-snapshot bloat (105K rows in 6 weeks → target ~30K
period rows).

```sql
CREATE TABLE alt.political_committee_memberships (
    country_code                FixedString(2),
    congress_number             UInt16,
    committee_id                String,
    committee_entity_id         UUID,                              -- FK ref.entities (entity_type='committee')
    bioguide_id                 String,
    legislator_entity_id        UUID,                              -- FK ref.entities (entity_type='person_legislator')
    role                        LowCardinality(String),            -- 'chair','ranking','vice_chair','member','ex_officio'
    majority_status             LowCardinality(String),            -- 'majority','minority'
    rank                        UInt16,                            -- seniority rank within committee
    effective_from              Date,                              -- SCD-2
    effective_to                Nullable(Date),                    -- NULL = current
    source, raw_id, ingest_run_id,
    as_of_time, ingested_at, version
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (country_code, congress_number, committee_id, bioguide_id, effective_from);
```

Point-in-time query pattern (was Sen. X on committee Y on trade date Z):
```sql
WHERE bioguide_id = 'X000123' AND committee_id = 'HSAP'
  AND effective_from <= '2024-06-15'
  AND (effective_to IS NULL OR effective_to > '2024-06-15')
```

### `alt.political_committees`

```sql
CREATE TABLE alt.political_committees (
    committee_id                String,                            -- source-native
    committee_entity_id         UUID,                              -- FK ref.entities (entity_type='committee')
    country_code                FixedString(2),
    congress_number             UInt16,
    parent_committee_id         Nullable(String),
    chamber                     LowCardinality(String),
    name                        String,
    jurisdiction                String,
    url                         String,
    is_subcommittee             Bool,
    effective_from              Date,                              -- SCD-2 for renaming/merging
    effective_to                Nullable(Date),
    source, raw_id, ingest_run_id,
    as_of_time, ingested_at, version
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (committee_id, effective_from);
```

### `alt.political_lobbying`, `alt.political_contracts`, `alt.political_fec_contributions`

Same shape template. All resolve to (`entity_id`, `security_id` when applicable,
`legislator_entity_id` for legislators). All FK canonical dims. All SCD-2 where
attributes change over time. Detailed schemas defined in Wave 4 build-out.

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
    mentioned_listings   Array(UUID),                  -- FK ref.listings, resolved from body
    mentioned_entities   Array(UUID),                  -- FK ref.entities
    ticker_confidence    Array(Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5)),
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

Reserve names; build when data flows. All follow the same
canonical-FK-plus-provenance pattern.

---

## 7. `book` — PM-authored investment intent

**The "book" is standard multi-manager hedge-fund vocabulary** — the PM owns
the book, and everything in this namespace is human-authored intent, not
computed data. Strategies (how we trade), themes (what we're betting on in the
world), trade names (individual expressions of a theme), trade legs (target
composition per trade name), and attribution (which fill belongs to which
trade name). None of this can be recomputed from raw facts; every row here is
an editorial act. Kept out of `derived.*` for exactly that reason.

### `book.strategies`

**Why this exists**: The trade engine will run multiple strategies through one or more broker accounts. Every execution, cash flow, and open order needs an attribution key — *which strategy generated this?* Without a strategies registry, PnL slicing by strategy is impossible and "how did strategy X do this week on paper" has no answer. Also the anchor for `derived.forward_test_attribution`. Moved from `derived.strategies` in rev 7 — strategies are authored, not computed.

```sql
CREATE TABLE book.strategies (
    strategy_id             UUID,                    -- factorlab canonical
    name                    String,
    description             String,
    -- lifecycle
    backtest_start_date     Nullable(Date),
    paper_start_date        Nullable(Date),
    live_start_date         Nullable(Date),
    retired_date            Nullable(Date),
    -- accounts (DENORMALIZED for query speed; canonical link is ref.broker_accounts.strategy_ids per F17 rev 11)
    paper_account_ids       Array(String),           -- FK ref.broker_accounts — inverse denorm of
                                                     --   ref.broker_accounts.strategy_ids; nightly
                                                     --   meta.integrity_checks asserts both sides agree
    live_account_ids        Array(String),           -- same as above for live accounts
    -- reproducibility
    config_hash             FixedString(64),         -- config version pin for backtest reproducibility
    code_version            LowCardinality(String),  -- git sha at strategy freeze
    universe_id             LowCardinality(String),  -- which ref.universes it operates on
    -- audit
    author_entity_id        Nullable(UUID),          -- who authored it (points at ref.entities for a person)
    active                  Bool,
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (strategy_id);
```

### `book.themes`

**Why this exists**: Strategies express *how* we trade (mean-reversion, cross-sectional value, event-driven). Themes express *what* we're betting on in the world (AI infrastructure buildout, GLP-1 healthcare disruption, US onshoring). One theme can be expressed by many trade names across many strategies; one strategy can express many themes. Without a themes dim, "how is the AI theme doing this week" requires a name-regex over positions and never rolls up cleanly. Risk numbers live in `risk.budgets` — this dim stays a pure descriptor.

```sql
CREATE TABLE book.themes (
    theme_id                UUID,                    -- factorlab canonical
    name                    String,                   -- 'ai_infra_buildout','glp1_disruption','us_onshoring'
    description             String,
    thesis                  String,                   -- the actual investment thesis (long-form)
    -- lifecycle
    inception_date          Date,
    retired_date            Nullable(Date),
    -- hierarchy (macro themes contain sub-themes; NULL for top-level)
    parent_theme_id         Nullable(UUID),           -- FK book.themes (self-ref)
    -- classification
    tags                    Array(LowCardinality(String)),  -- 'macro','sectoral','event','structural','china_exposure','ai','glp1',...
    -- ownership
    author_entity_id        Nullable(UUID),           -- FK ref.entities (person who originally authored the thesis)
    owner_entity_id         Nullable(UUID),           -- FK ref.entities (current sponsor / responsible PM; may differ after handoff) — added rev 8
    -- audit
    active                  Bool,
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (theme_id);
```

**Hierarchy note**: `parent_theme_id` enables `("compute buildout" → "AI compute buildout")` chains. Two-level max in practice — deeper trees signal a taxonomy problem, not a data model problem. Rollups (`broker.exposure_by_theme_daily`) traverse one level by joining self.

### `book.trade_names`

**Why this exists**: A trade_name is the *unit of investment thesis* — a coherent set of position legs expressing one named bet. "Long NVDA, short AMD on datacenter capex" is one trade_name, even though it lives across two symbols and two sides. Without this table, PnL attribution stops at the position level and you cannot answer *"did the pair thesis work — did the NVDA-vs-AMD spread pay off?"* Trade names sit under a theme and (optionally) under a strategy — a discretionary macro trade_name may have no strategy but is still a theme expression. The word `trade` alone is banned as an entity name (too overloaded with executions/fills); we always say `trade_name`.

```sql
CREATE TABLE book.trade_names (
    trade_name_id           UUID,                    -- factorlab canonical
    name                    String,                   -- 'nvda_amd_datacenter_capex_q3_2026'
    -- hierarchy
    theme_id                UUID,                    -- FK book.themes (required)
    strategy_id             Nullable(UUID),          -- FK book.strategies (NULL = discretionary)
    -- shape
    side                    LowCardinality(String),  -- 'long_only','short_only','pair','basket',
                                                      --   'long_short','event_driven'
    base_currency           FixedString(3),           -- ISO-4217; the ccy the PM authored the thesis in
                                                      --   (added rev 8) — enables MTM PnL in the "native"
                                                      --   ccy for the trade_name (a JPY-only pair trade
                                                      --   is priced in JPY, not fund base). NULL not allowed —
                                                      --   PM must pick one at creation.
    priority                Enum8('high'=1,'medium'=2,'low'=3,'watch'=4),  -- conviction rating (added rev 8);
                                                      --   PM dashboard groups by; alert thresholds tighten on 'high'
    -- lifecycle
    inception_date          Date,
    target_hold_horizon_days Nullable(UInt32),        -- planned duration; NULL = open-ended
    -- thesis / exit
    thesis                  String,                   -- what we're betting on
    exit_criteria           String,                   -- what would make us close
    -- state
    status                  LowCardinality(String),  -- 'draft','active','paused','closed','stopped_out'
    -- ownership (opened_by = original author; owner = current runner, may differ after handoff — added rev 8)
    opened_by_entity_id     Nullable(UUID),           -- FK ref.entities
    owner_entity_id         Nullable(UUID),           -- FK ref.entities (currently responsible PM)
    closed_at               Nullable(DateTime64(3, 'UTC')),
    close_reason            Nullable(String),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (trade_name_id);
```

**Trade_name vs strategy**: a strategy is a repeatable procedure that generates many trade_names over its life. A trade_name is one instance of a named bet. `book.trade_names.strategy_id` is nullable because a discretionary PM trade_name (e.g. "add to LLY on the pullback") has a theme but no procedural parent.

### `book.trade_legs`

**Why this exists**: `book.trade_names` describes the bet. `book.trade_legs` describes *what the PM wants the trade_name to look like* — target weights, entry prices, stops, take-profits per leg. This is intent, not fills. Actual fills live in `broker.executions`. A discretionary single-name trade_name has one leg, a pair has two, a basket has N. Reconciliation of intent vs actual ("we're 30% underweight the AMD short leg — trade engine hasn't caught up") is the join between this table and `broker.positions_snapshot` filtered by `trade_name_id`. Trade legs are versioned SCD-2 style so the PM can amend targets ("bump NVDA target from 40% to 50% after earnings") without losing the original intent.

```sql
CREATE TABLE book.trade_legs (
    trade_name_id           UUID,                    -- FK book.trade_names
    listing_id              UUID,                    -- FK ref.listings
    security_id             UUID,                    -- FK ref.securities (denorm for query speed)
    -- intent
    leg_side                LowCardinality(String),  -- 'long' | 'short' — renamed from `side` rev 11
                                                      --   (F22) to disambiguate from `book.trade_names.side`
                                                      --   which uses a wholly different enum
                                                      --   ('long_only','pair','basket',...)
    target_weight_pct       Decimal(8,4),             -- % of trade_name notional
    target_notional         Nullable(Decimal(20,6)), -- absolute; NULL if only pct is set
    target_notional_ccy     Nullable(FixedString(3)),
    -- price levels
    entry_price_target      Nullable(Decimal(20,6)),
    stop_price              Nullable(Decimal(20,6)),
    take_profit_price       Nullable(Decimal(20,6)),
    -- realized entry (added rev 8) — fill-weighted average populated by the attribution resolver
    -- so trade_name-level MTM PnL doesn't re-aggregate broker.executions on every read.
    entered_at_price_avg    Nullable(Decimal(20,6)),
    -- rationale
    rationale               String,
    -- validity window (SCD-2)
    effective_from          DateTime64(3, 'UTC'),
    effective_to            Nullable(DateTime64(3, 'UTC')),
    -- audit
    set_by_entity_id        Nullable(UUID),
    ingest_run_id           UUID,
    as_of_time              DateTime64(3, 'UTC'),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(effective_from)
ORDER BY (trade_name_id, listing_id, effective_from);
```

**Distinct from `broker.positions_snapshot`**: this is the *target*, that is the *actual*. The delta is executable work for the trade engine (or discretionary follow-up for the PM).

### `book.attribution`

**Why this exists**: `broker.executions.strategy_id` (added rev 5) tells you which strategy generated a fill. It does *not* tell you which trade_name under that strategy the fill belongs to — a mean-reversion strategy can be running five pair trade_names simultaneously and a single AMD fill could belong to any of them. Without this bridge, trade_name-level PnL requires guessing from timestamp + ticker, and any theme-level rollup requires the same guess. This table records the definitive `execution → trade_name → theme → strategy` chain, populated by rule when possible (trade engine writes `trade_name_id` at order-placement time) or by hand when discretionary. Renamed from `derived.position_attribution` in rev 7.

```sql
CREATE TABLE book.attribution (
    execution_id            String,                   -- FK broker.executions.exec_id
    -- assignment
    trade_name_id           UUID,                    -- FK book.trade_names
    -- denormalized for query speed (must match book.trade_names at assigned_at)
    theme_id                UUID,                    -- FK book.themes
    strategy_id             Nullable(UUID),          -- FK book.strategies (matches trade_names.strategy_id)
    -- audit
    assigned_at             DateTime64(3, 'UTC'),
    assigned_by             LowCardinality(String),  -- 'rule_trade_engine','rule_order_ref_tag',
                                                      --   'manual_pm','manual_ops','backfill_heuristic'
    confidence              Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    rationale               String,
    -- lineage
    computation_id          Nullable(UUID),           -- FK derived.computations — resolver run that produced this
                                                      --   row (added rev 8). NULL for manual assignments.
    ingest_run_id           UUID,
    as_of_time              DateTime64(3, 'UTC'),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(assigned_at)
ORDER BY (trade_name_id, execution_id, assigned_at);
```

**Population paths**:
1. **Trade engine (preferred)** — when placing an order, tag it with `trade_name_id` via `Order.orderRef` (e.g. `orderRef='trade_name:<uuid>'`). Ingester reads back, writes `book.attribution` with `assigned_by='rule_trade_engine'`, `confidence='exact'`.
2. **Rule-based** — for un-tagged fills, a resolver runs nightly: if the fill's `(strategy_id, listing_id, side)` matches exactly one active trade_name's leg, assign with `confidence='high'`.
3. **Manual** — PM assigns via internal tool; `confidence='manual_override'`.
4. **Unassignable** — fill remains without a row here; `assigned_by IS NULL`. Surfaces as "unattributed fills" on the ops dashboard.

Every `broker.executions` row *should* eventually get one `book.attribution` row. Coverage is a monitored metric.

**Open-order attribution — no bridge table** (added rev 11 per F12): open orders
are attributed at placement time via `orderRef='trade_name:<uuid>'` — the trade
engine writes `trade_name_id` directly into `broker.open_orders_snapshot`. There
is no attribution-history table for open orders because the mapping is
deterministic from `orderRef` and open orders are ephemeral — they either fill
(and executions receive attribution via `book.attribution`) or cancel (with no
lasting attribution record needed). If `orderRef` is missing or malformed, the
open order lands with `trade_name_id IS NULL` and surfaces in
`meta.unresolved_entities` for manual review, same as any other resolver miss.
This is a deliberate asymmetry with fills — one path (`book.attribution`) has
a replayable history because fills are the durable, PnL-bearing artifacts;
the other (open orders) trusts `orderRef` because the artifact itself is
transient. If resolver logic ever changes materially, historical fills replay
through `book.attribution` (write new rows with `computation_id` linking to
the resolver run); historical open orders don't need replay because they're
already resolved into executions or gone.

### 7.7 Connectivity audit (rev 8)

Full column and FK walk through the fund-structure layer as one connected
system: `book.strategies`, `book.themes`, `book.trade_names`,
`book.trade_legs`, `book.attribution`, `risk.budgets`,
`broker.positions_snapshot`, `broker.executions`,
`broker.open_orders_snapshot`, `broker.order_events`, and
`derived.forward_test_attribution`. Purpose of the audit: (a) verify every
declared FK has a matching PK of compatible type, (b) verify denormalized
columns are consistent across tables that carry them, (c) verify PIT columns
are present on every fact row and versioning columns on every dim,
(d) verify sort keys match the dominant query, (e) prove both join paths
(top-down and bottom-up) work in ≤ 1–2 joins, (f) prove point-in-time budget
lookup works via SCD-2, (g) surface any column a PM would actually need at
query time that's missing.

#### Per-table audit

| Table | Column coverage | FK integrity | Sort-key rationale | Columns added rev 8 |
|---|---|---|---|---|
| `book.strategies` | Complete. Lifecycle dates, config_hash / code_version for reproducibility, paper + live account arrays denormed, author. | `paper_account_ids` / `live_account_ids` are `Array(String)` — no in-engine check; nightly integrity job validates each element resolves in `ref.broker_accounts`. `author_entity_id` → `ref.entities`. Both compatible. | `ORDER BY (strategy_id)` — dim table, lookups by canonical UUID; correct. | none |
| `book.themes` | Complete after audit. `parent_theme_id` self-ref for two-level hierarchy, `tags` for cross-cutting queries (`'china_exposure'`, `'ai'`, `'glp1'`), author. Owner was missing — added. | `parent_theme_id` → `book.themes.theme_id` (same table). `author_entity_id`, `owner_entity_id` → `ref.entities`. All UUID → UUID; compatible. | `ORDER BY (theme_id)` — dim, correct. | `owner_entity_id` |
| `book.trade_names` | Complete after audit. Was missing (a) currency (multi-currency book support), (b) conviction rating (PM dashboards want it), (c) current owner vs opener. All three added. | `theme_id` → `book.themes.theme_id`, `strategy_id` → `book.strategies.strategy_id`, `opened_by_entity_id` / `owner_entity_id` → `ref.entities`. All UUID → UUID; compatible. | `ORDER BY (trade_name_id)` — dim, correct. | `base_currency`, `priority`, `owner_entity_id` |
| `book.trade_legs` | Complete after audit. Was missing realized entry basis at leg level — added. Everything else present (targets, stops, TP, SCD-2 window, rationale). | `trade_name_id` → `book.trade_names.trade_name_id`, `listing_id` → `ref.listings.listing_id`, `security_id` → `ref.securities.security_id`, `set_by_entity_id` → `ref.entities`. All UUID → UUID; compatible. | `ORDER BY (trade_name_id, listing_id, effective_from)` — the dominant query is "all target legs for trade_name X as of time T", which prunes tightly here. Partition by month of `effective_from` supports historical target lookback. Correct. | `entered_at_price_avg` |
| `book.attribution` | Complete after audit. Was missing explicit lineage (`computation_id`) to the resolver run that produced rule-based rows. Every other required column present: `execution_id`, `trade_name_id`, `theme_id` denorm, `strategy_id` denorm, `assigned_at`, `assigned_by`, `confidence`, `rationale`. | `execution_id` → `broker.executions.exec_id` (String → String; compatible). `trade_name_id` → `book.trade_names.trade_name_id` (UUID). `theme_id` → `book.themes.theme_id` (UUID). `strategy_id` → `book.strategies.strategy_id` (UUID). `computation_id` → `derived.computations.computation_id` (UUID). All compatible. | `ORDER BY (trade_name_id, execution_id, assigned_at)` — the primary query surface is "give me every execution attributed to trade_name X" → leads with `trade_name_id`; correct. Secondary "give me the attribution for execution Y" prunes via `execution_id` inside the leading-`trade_name_id` group; acceptable (bloom-filter index optional). | `computation_id` |
| `risk.budgets` | Complete. Polymorphic `(scope, scope_id)` covers fund/strategy/theme/trade_name from one schema; SCD-2 window (`effective_from` / `effective_to`); every budget metric nullable so callers set only what applies to the scope. | `scope_id` is `String` (holds UUID-as-text) because scope is polymorphic — validation done at ingest by dispatching on `scope`. `set_by_entity_id` → `ref.entities` (UUID). Type-compatible where FKs are concrete. | `ORDER BY (scope, scope_id, effective_from)` + `PARTITION BY (scope, toYYYYMM(effective_from))` — dominant query is "the effective budget for (scope, scope_id) as of date D"; leads correctly. | none |
| `broker.positions_snapshot` | Complete. Carries `trade_name_id` + `theme_id` denorms per §11.12 for hot dashboard read. FX-PIT and per-position margin per rev 5. | `trade_name_id` → `book.trade_names` (UUID). `theme_id` → `book.themes` (UUID). Both denormalized, so nullable; consistent with §11.12. | `ORDER BY (broker_code, account_id, snapshot_time, coalesce(listing_id, ...), vendor_id)` — dominant query is "positions for (broker, account) as of time T"; correct. | none |
| `broker.executions` | Complete. `strategy_id` (rev 5) + `trade_name_id` (rev 7). Consistent with §11.12. Fill grain data intact. | `strategy_id` → `book.strategies`, `trade_name_id` → `book.trade_names`, plus canonical `listing_id`/`security_id`/`contract_id`. All UUID → UUID; compatible. | `ORDER BY (broker_code, account_id, exec_time, exec_id)` — dominant query is "fills for (broker, account) in a time window"; correct. `trade_name_id` lookups use the bloom filter on `listing_id` + secondary filter. | none |
| `broker.open_orders_snapshot` | Complete. `strategy_id` (rev 5) + `trade_name_id` (rev 7). Consistent with §11.12. | Same FKs as `broker.executions`. Compatible. | `ORDER BY (broker_code, account_id, snapshot_time, perm_id)` — dominant query is "open orders for (broker, account) as of snapshot"; correct. | none |
| `broker.order_events` | Complete. `strategy_id` + `trade_name_id`. Full order lifecycle. Consistent with §11.12. | Same FKs. `perm_id` (Int64) is the durable join key to `executions` / `open_orders_snapshot`; both sides Int64. | `ORDER BY (broker_code, account_id, perm_id, event_seq)` — dominant query is the event tape for one order; correct. | none |
| `derived.forward_test_attribution` | Complete. Per-strategy per-day paper-vs-backtest scorecard. `strategy_id` now points at `book.strategies` per rev 7. | `strategy_id` → `book.strategies` (UUID → UUID; compatible). `account_id` → `ref.broker_accounts` (String → String; compatible). `computation_id` → `derived.computations` (UUID → UUID). | `ORDER BY (strategy_id, trade_date, account_id)` — dominant query is "strategy X paper divergence over time"; correct. | none |

`broker.cash_flows` and `broker.applied_corporate_actions` are deliberately
NOT extended with `trade_name_id` — reason preserved from §11.12: dividend
attribution by trade_name is rarely useful (themes/strategies are the right
grain) and applied-corporate-actions are per-account not per-trade_name.

#### PIT / version column coverage

- Every fact row above has `as_of_time DateTime64(3, 'UTC')`, `version UInt64`,
  and `ingested_at DateTime64(3, 'UTC')`. Verified.
- SCD-2 windowed tables (`book.trade_legs`, `risk.budgets`) additionally have
  `effective_from` / `effective_to`. Nightly no-overlap check per
  `(trade_name_id, listing_id)` and per `(scope, scope_id)` respectively.
- Dim tables (`book.strategies`, `book.themes`, `book.trade_names`) carry
  `version` + `ingested_at`. `ReplacingMergeTree(version)` collapses to
  latest edit. Verified.

#### Hierarchy walk — top-down (theme → executions)

Given a `theme_id`, get every executed fill under it:

```sql
SELECT e.exec_id, e.exec_time, e.side, e.quantity, e.price,
       tn.name AS trade_name, tn.priority, s.name AS strategy
FROM   book.themes                t
JOIN   book.trade_names           tn ON tn.theme_id = t.theme_id
LEFT   JOIN book.strategies       s  ON s.strategy_id = tn.strategy_id
JOIN   book.attribution           a  ON a.trade_name_id = tn.trade_name_id
JOIN   broker.executions          e  ON e.exec_id = a.execution_id
WHERE  t.theme_id = {theme_id:UUID}
  AND  tn.status = 'active';
```

Four joins, all on canonical UUIDs. Positions rollup is identical with
`broker.positions_snapshot` filtered on `snapshot_time` in place of the
`executions` join. Legs use `book.trade_legs` for target vs
`broker.positions_snapshot` for actual — reconciliation join in one hop.

#### Hierarchy walk — bottom-up (execution → risk budgets)

Given a `broker.executions.exec_id`, get its full attribution chain and the
risk budget effective at fill time at every scope:

```sql
WITH
  attr AS (
    SELECT trade_name_id, theme_id, strategy_id
    FROM   book.attribution
    WHERE  execution_id = {exec_id:String}
    ORDER BY version DESC LIMIT 1
  ),
  exec AS (
    SELECT exec_time
    FROM   broker.executions
    WHERE  exec_id = {exec_id:String}
    ORDER BY version DESC LIMIT 1
  )
SELECT b.scope, b.scope_id,
       b.gross_exposure_pct_cap, b.net_exposure_pct_cap,
       b.var_1d_bps_cap, b.max_drawdown_pct, b.stop_loss_pct
FROM   risk.budgets b, attr, exec
WHERE  ( (b.scope='fund'        AND b.scope_id = 'default_fund')
      OR (b.scope='strategy'    AND b.scope_id = toString(attr.strategy_id))
      OR (b.scope='theme'       AND b.scope_id = toString(attr.theme_id))
      OR (b.scope='trade_name'  AND b.scope_id = toString(attr.trade_name_id)) )
  AND  b.effective_from <= exec.exec_time
  AND  (b.effective_to IS NULL OR b.effective_to > exec.exec_time);
```

Two joins (execution → attribution, then a polymorphic budget lookup).
Point-in-time correct because the `effective_from`/`effective_to` window is
checked against the fill's `exec_time`, not `now()`. Any budget change
after the fill does not affect the historical answer.

#### PM-usability gaps closed by rev 8

- **Currency of trade_name** — was missing; added `book.trade_names.base_currency`. Rationale: a JPY-only pair trade should be MTM'd in JPY.
- **Conviction rating** — was missing; added `book.trade_names.priority`. Rationale: PM dashboard groups active trade_names by conviction; alerting thresholds tighten on `'high'`.
- **Current owner vs opener** — `opened_by_entity_id` records the author; `owner_entity_id` records the current runner (rev 8). Rationale: trade_names get handed off between PMs; both provenance and current responsibility need to be queryable.
- **Theme sponsor** — added `book.themes.owner_entity_id`. Rationale: "themes I own" filter for a PM dashboard.
- **Cross-cutting theme tags** — already present as `book.themes.tags Array(LowCardinality(String))`; audit confirmed no addition needed. `WHERE has(tags,'china_exposure')` works today.
- **Trade_name entry basis at leg level** — added `book.trade_legs.entered_at_price_avg`. Rationale: MTM PnL per leg without re-aggregating `broker.executions` on every read; populated by the attribution resolver at fill time.
- **Attribution lineage** — added `book.attribution.computation_id` → `derived.computations`. Rationale: rule-based assignments were untraceable to their resolver run; manual assignments continue to leave it NULL.

#### FK-integrity summary

Zero FK type mismatches. Every UUID FK targets a UUID PK. Every String FK
(`execution_id` → `broker.executions.exec_id`, `account_id` →
`ref.broker_accounts.account_id`, `scope_id` polymorphic) targets a String
PK. Nightly `meta.integrity_checks` scans all denorm FKs for dangling
references; alerts on breach.

---

## 8. `risk` — Risk-owned governance

**Owned by risk, not by PM.** In multi-manager funds, PMs run the book but a
separate risk function sets and enforces the constraints — gross/net caps,
VaR budgets, drawdown stops, concentration limits. Modeling this as a
distinct namespace makes ownership legible (grants can differ), keeps PM
edits from silently changing risk state, and leaves clean room to grow the
namespace. **Roadmap** (not built now): `risk.limits` (hard vs soft
enforcement policy), `risk.exposure_snapshots` (nightly denormalized current
exposure per scope, refreshes cross-scope aggregation dashboards),
`risk.var_daily` (parametric + historical VaR per scope), `risk.drawdown_events`
(hit-and-recovery log per scope). Rev 7 ships only `risk.budgets`.

### `risk.budgets`

**Why this exists**: Every level of the fund hierarchy needs a risk allocation — the fund has a gross-exposure cap, a strategy has a VaR budget, a theme has a max drawdown before we review, a trade_name has a stop-loss. These budgets change over time (halved GLP-1 exposure after the LLY drawdown, doubled AI budget after the NVDA re-rating) — the table must be versioned. A single polymorphic table keyed on `(scope, scope_id)` beats one-table-per-scope because (a) risk budget schema is identical at every level (gross/net/VaR/max-position/max-DD/stop-loss/concentration), and (b) rollup queries can `UNION` cleanly across scopes. The alternative — four tables — duplicates schema and forces every downstream query to know which table to hit. Moved from `derived.risk_budgets` in rev 7 — risk budgets are a governance act, not a computation.

```sql
CREATE TABLE risk.budgets (
    -- scope
    scope                       LowCardinality(String),   -- 'fund','strategy','theme','trade_name'
    scope_id                    String,                    -- UUID as string; for scope='fund' the sentinel
                                                            --   'default_fund' is used until ref.funds exists;
                                                            --   for the other three scopes this is the UUID from
                                                            --   book.strategies / book.themes / book.trade_names
    -- validity window (SCD-2)
    effective_from              DateTime64(3, 'UTC'),
    effective_to                Nullable(DateTime64(3, 'UTC')),  -- NULL = currently active
    -- budget metrics (all optional — set what applies to the scope)
    gross_exposure_pct_cap      Nullable(Decimal(8,4)),   -- max gross / NAV
    net_exposure_pct_cap        Nullable(Decimal(8,4)),   -- max |net| / NAV
    var_1d_bps_cap              Nullable(Decimal(8,2)),   -- 1-day 95% VaR in basis points of NAV
    max_position_pct            Nullable(Decimal(8,4)),   -- max single-name weight
    max_drawdown_pct            Nullable(Decimal(8,4)),   -- soft stop at scope level
    stop_loss_pct               Nullable(Decimal(8,4)),   -- hard stop at scope level
    concentration_limit         Nullable(Decimal(8,4)),   -- e.g. max sector weight
    -- audit
    set_by_entity_id            Nullable(UUID),           -- FK ref.entities
    rationale                   String,
    -- lineage
    ingest_run_id               UUID,
    as_of_time                  DateTime64(3, 'UTC'),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
PARTITION BY (scope, toYYYYMM(effective_from))
ORDER BY (scope, scope_id, effective_from);
```

**SCD-2 semantics**: setting a new budget writes two rows atomically — old row's `effective_to` gets set to `now()`, new row inserted with `effective_from = now()` and `effective_to = NULL`. Same no-overlap test as `ref.universe_membership` applies (per `(scope, scope_id)`). The `'fund'` scope uses a fixed sentinel `scope_id='default_fund'` until a formal `ref.funds` dim is introduced — this is fine for a single-fund book; a future rev adds `ref.funds` when a second fund appears.

**Rollup check** (downstream — not a table, a query): sum of trade_name-level `max_position_pct * trade_name_notional` at any point should be ≤ theme-level cap, and theme sums ≤ strategy cap, and strategy sums ≤ fund cap. A nightly `meta.integrity_checks` entry flags budget-hierarchy inversions.

**Position-level stops are NOT `risk.budgets`** (added rev 11 per F14). A
single-name tactical stop-loss on a specific leg — *"close NVDA if it hits
$180"* — lives in `book.trade_legs.stop_price`, not in `risk.budgets`. That's
PM-owned intent at the leg level, not risk-committee governance. `risk.budgets`
covers scopes at which a governance committee actually sets caps (fund,
strategy, theme, trade_name) — allocation limits, VaR budgets, aggregate
drawdown stops, exposure caps. The single-name price stop is tactical
execution intent authored by the PM at trade construction time, and it lives
in the `book` namespace precisely because it moves with the trade thesis, not
with risk policy. This split is deliberate: do not add `'position'` to the
scope enum without a written governance rationale for why the risk committee
now owns single-name stops. If the ownership boundary shifts, the schema
change follows the governance change, not the other way around.

---

## 9. `derived` — Reproducible computed data

Strictly *computed* data: factors, signals, portfolios, backtests, and the
forward-test scorecard. Human-authored inputs (strategies, themes, trade
names, trade legs, execution attribution) live in `book.*`; risk-governance
constraints live in `risk.*`. See §7 and §8.

### `derived.forward_test_attribution`

**Why this exists**: Paper trading only earns its keep if you can score its performance against the backtest that predicted it. This table is the single query surface for *"strategy X, paper return - backtest return, per day"* — the primary forward-test health check. Populated daily from `broker.nav_daily` (paper account associated with the strategy) joined against `derived.backtest_returns` for the same strategy on the same day. `strategy_id` is the canonical FactorLab UUID from `book.strategies` (rev 7).

```sql
CREATE TABLE derived.forward_test_attribution (
    trade_date              Date,
    strategy_id             UUID,                    -- FK book.strategies
    broker_code             LowCardinality(String),
    account_id              String,                  -- paper account, FK ref.broker_accounts
    base_currency           FixedString(3),          -- denormalized from ref.broker_accounts
    -- returns
    paper_nav_return        Nullable(Float64),       -- from broker.nav_daily day-over-day
    backtest_return         Nullable(Float64),       -- from derived.backtest_returns
    divergence              Nullable(Float64),       -- paper - backtest
    divergence_pct          Nullable(Float64),       -- divergence / |backtest|
    -- context
    paper_nav               Nullable(Decimal(20,6)),
    backtest_notional       Nullable(Decimal(20,6)),
    n_executions_today      UInt32,
    -- lineage
    computation_id          UUID,                    -- FK derived.computations
    computed_at             DateTime64(3, 'UTC'),
    as_of_time              DateTime64(3, 'UTC'),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (strategy_id, trade_date, account_id);
```

Alerting rule (built downstream): `|divergence_pct| > threshold` for N consecutive days flags a strategy for review.

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

## 10. `meta` — Data observability

The data about our data — freshness, volume, coverage, lineage, drift, and
entity-resolution failures. Every pipeline health signal, every "did today's
ingest run land clean," every "why does this fact-row have a NULL FK" ends up
here. This is the observability layer for the data platform, not the trading
ops layer (execution / order routing / risk-controls live in the trade engine,
not here). The namespace name follows the industry data-observability framing
(freshness / volume / schema / distribution / lineage) rather than "ops," which
overloaded to trading operations.

### `meta.ingestion_runs`

```sql
CREATE TABLE meta.ingestion_runs (
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

### `meta.expected_series`, `meta.session_coverage`, `meta.recovery_state`, `meta.source_status`

Unified across countries (was `us_*` + `india_*`). All FK `listing_id`.

### `meta.lineage`

```sql
CREATE TABLE meta.lineage (
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
rely on `meta.ingestion_runs.listing_ids_touched` for run-level lineage.
Per-row lineage becomes opt-in only for datasets where it earns its keep
(fundamentals, derived).

### `meta.reconciliation_drift`

**Why this exists**: The reconciliation contract in §11.7 states *"FactorLab detects drift, the trade engine acts on it."* Without a queryable table, drift detection produces logs but no durable record — the trade engine has nothing to consume. This is the join surface: the trade engine's boot check is `SELECT count(*) FROM meta.reconciliation_drift WHERE broker_code=? AND account_id=? AND resolved_at IS NULL`.

```sql
CREATE TABLE meta.reconciliation_drift (
    detected_at         DateTime64(3, 'UTC'),
    broker_code         LowCardinality(String),
    account_id          String,
    account_mode        LowCardinality(String),
    drift_type          LowCardinality(String),   -- 'position_qty','position_missing','position_extra',
                                                   --   'nav','cash','open_order'
    security_key        Nullable(String),          -- vendor_id or listing_id::text for position drifts
    prev_snapshot_time  DateTime64(3, 'UTC'),      -- snapshot we compared against
    curr_snapshot_time  DateTime64(3, 'UTC'),      -- snapshot that revealed the drift
    expected_value      Nullable(String),          -- string-typed to accept any drift shape
    observed_value      Nullable(String),
    delta               Nullable(String),          -- pre-computed human-readable diff
    severity            LowCardinality(String),    -- 'info','warn','critical'
    -- resolution audit
    resolved_at         Nullable(DateTime64(3, 'UTC')),
    resolved_by         Nullable(LowCardinality(String)),  -- 'auto_next_snapshot','manual','ignore'
    resolution_note     Nullable(String),
    -- lineage
    ingest_run_id       UUID,
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, toYYYYMM(detected_at))
ORDER BY (broker_code, account_id, detected_at);
```

Corporate-action-driven position changes are recorded in `broker.applied_corporate_actions` (§11.10) and DO NOT create a drift row — the reconciler consults that table first and treats matched deltas as expected.

### `meta.unresolved_entities`

**Why this exists**: Every ingester (broker, political, market data) that maps a vendor ID → canonical FactorLab UUID will encounter misses — new tickers, renamed instruments, freshly-onboarded senators, unknown option OCC symbols. §13.9 (Entity resolution failure policy) states "never silently drop" — unresolved rows land here for review + resolver-next-pass retry. Also the source table backing the `resolution_confidence='unresolved'` values across every fact table.

```sql
CREATE TABLE meta.unresolved_entities (
    first_seen          DateTime64(3, 'UTC'),
    last_seen           DateTime64(3, 'UTC'),
    source              LowCardinality(String),   -- FK ref.sources (which ingester surfaced it)
    alias_kind          LowCardinality(String),   -- matches ref.identifier_aliases.alias_kind
    alias_value         String,                    -- the raw vendor id
    scope_country       Nullable(FixedString(2)),
    scope_exchange      Nullable(LowCardinality(String)),
    context_json        String,                    -- caller-provided context (row hash, security_type guess, etc.)
    occurrence_count    UInt32,                    -- how many facts referenced this unresolved id
    -- resolution attempts
    retry_count         UInt32,
    last_retry_at       Nullable(DateTime64(3, 'UTC')),
    resolved_at         Nullable(DateTime64(3, 'UTC')),
    resolved_target_kind Nullable(LowCardinality(String)),  -- 'entity','security','listing','contract'
    resolved_target_id   Nullable(UUID),
    resolved_by          Nullable(LowCardinality(String)),  -- 'auto_resolver','manual_override'
    resolution_note      Nullable(String),
    version, ingested_at
) ENGINE = ReplacingMergeTree(version)
ORDER BY (source, alias_kind, alias_value);
```

Unresolved rows persist until resolved — `resolved_at IS NULL` is the review queue. Once resolved, the corresponding `ref.identifier_aliases` row is written (with `source='meta.unresolved_entities_reviewer'` for provenance) and the fact-side rows get their canonical FK backfilled on the next resolver pass.

---

## 11. `broker` — Portfolio and execution monitoring

Read-only mirror of broker-side truth. Positions, account state, executions,
and open orders across every broker account (paper and live) as time series.
Every table lets you answer *"what did the broker say we owned / owed / had done,
as of time T"* without reconstruction.

**FactorLab code writing here is strictly read-only against the broker's API** —
no `placeOrder` / `cancelOrder` calls in this codebase. Execution runs in a
separate trade-engine service; the engine places orders (on paper for forward
testing, on live for production) and reads back from `broker.executions` to
close its own attribution loop. FactorLab only *watches*.

### Dimensions carried on every row

| Column | Type | Notes |
|---|---|---|
| `broker_code` | `LowCardinality(String)` | `ibkr`; later `schwab_broker`, etc. |
| `account_id` | `String` | Broker-native (paper `DUE375963`, live `U1234567`) |
| `account_mode` | `LowCardinality(String)` | `paper` \| `live` |
| `country_code` | `FixedString(2)` | Account booking country |

Multi-account (both paper and live simultaneously) is a first-class case, not
an afterthought — forward testing lives on paper, real portfolio on live,
same tables, disambiguated by `account_mode`.

### 11.1 `broker.positions_snapshot`

Time series of positions. Cadence: pre-open snapshot, EOD snapshot, on-demand.

```sql
CREATE TABLE broker.positions_snapshot (
    -- identity
    snapshot_time      DateTime64(3, 'UTC'),
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    -- canonical FactorLab IDs (resolved from vendor id via ref.identifier_aliases)
    listing_id         Nullable(UUID),
    security_id        Nullable(UUID),
    contract_id        Nullable(UUID),                 -- FUT/OPT
    entity_id          Nullable(UUID),
    product_type       LowCardinality(String),
    -- vendor-native fallback (denormalized)
    vendor_id          String,                          -- IBKR conid as string
    trading_symbol     String,
    currency           LowCardinality(String),
    -- position
    position           Decimal(20,6),                   -- signed; negative = short
    avg_cost           Nullable(Decimal(20,6)),
    market_price       Nullable(Decimal(20,6)),
    market_value       Nullable(Decimal(20,6)),
    unrealized_pnl     Nullable(Decimal(20,6)),
    realized_pnl_ytd   Nullable(Decimal(20,6)),
    market_value_usd   Nullable(Decimal(20,6)),         -- FX-normalized at snapshot_time (broker-reported)
    -- FX PIT (added rev 5) — captures which FX rate produced market_value_usd, so historical NAV in
    -- another base currency is reproducible after market.fx_rates restatements
    fx_rate_to_base    Nullable(Decimal(18,8)),         -- e.g. USD→SGD if base_currency='SGD'
    fx_rate_source_time Nullable(DateTime64(3, 'UTC')), -- timestamp of the FX quote used
    -- margin contribution (added rev 5) — per-position margin usage from IBKR's model-portfolio scope
    initial_margin_contribution     Nullable(Decimal(20,6)),
    maintenance_margin_contribution Nullable(Decimal(20,6)),
    -- resolution audit (aligned to alt.* enum, §6)
    resolution_confidence Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    -- provenance + PIT
    source             LowCardinality(String),          -- always the broker: 'ibkr'
    source_channel     LowCardinality(String),          -- 'paper_gateway','live_gateway'
    raw_id             Nullable(UUID),
    ingest_run_id      UUID,
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    version            UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(snapshot_time))
ORDER BY (broker_code, account_id, snapshot_time,
          coalesce(listing_id, toUUID('00000000-0000-0000-0000-000000000000')),
          vendor_id);
```

**Why time series, not "current":** reconciliation, PnL attribution, and drawdown
reconstruction all need position-as-of-T queries. A replace-in-place table would
lose intra-session history.

**Why nullable canonical IDs:** positions can arrive faster than the resolver
maps `conid → listing_id`. Rows land with `resolution_confidence='vendor_only'`
and queue to `meta.unresolved_entities`; the resolver backfills canonical IDs on
its next pass, writing a new row with a later `as_of_time`.

### 11.2 `broker.account_state_snapshot`

Tall/long time series of account-level metrics. IBKR alone emits ~144 tags per
account across segment × currency dimensions (see
[`docs/data-sources/06-ibkr.md`](../data-sources/06-ibkr.md) §5.4). Tall keeps
schema stable when new tags appear.

```sql
CREATE TABLE broker.account_state_snapshot (
    snapshot_time      DateTime64(3, 'UTC'),
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    -- metric identity
    metric             LowCardinality(String),          -- vendor-native tag (IBKR: 'NetLiquidation',
                                                        --   'BuyingPower','MaintMarginReq',...)
    metric_canonical   LowCardinality(String),          -- factorlab-canonical (added rev 11 per F10);
                                                        --   populated at ingest from
                                                        --   ref.broker_metrics_map(broker_code, vendor_metric)
                                                        --   →canonical_metric; e.g. 'net_liquidation',
                                                        --   'buying_power','maint_margin_req'
    segment            LowCardinality(String),          -- ''|'S'|'C'|'P' (agg/Securities/Commodities/Paxos)
    currency           LowCardinality(String),          -- 'USD','BASE','NONE'
    value_num          Nullable(Decimal(24,6)),         -- numeric cast (majority path)
    value_str          Nullable(String),                -- when broker returns non-numeric
    -- provenance + PIT
    source, source_channel, raw_id, ingest_run_id,
    as_of_time, ingested_at, version
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(snapshot_time))
ORDER BY (broker_code, account_id, metric, segment, currency, snapshot_time);
```

### 11.3 `broker.executions`

Immutable event log — fills for orders placed by ANY client (trade engine,
manual GUI order, etc.). Each fill is one row keyed by the broker's exec ID.

```sql
CREATE TABLE broker.executions (
    exec_id            String,                          -- broker exec id (unique, immutable)
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    -- order identity
    order_id           Int64,                           -- broker session-scoped
    perm_id            Int64,                           -- broker-durable (join key)
    placed_by_client   Nullable(Int32),                 -- IBKR API clientId; e.g. trade-engine ID
    -- origin of order (added rev 5) — see ref.execution_methods for lookup logic
    execution_method_id LowCardinality(String),         -- FK ref.execution_methods
    order_ref          Nullable(String),                -- IBKR Order.orderRef free-text annotation
    route_pref         LowCardinality(String),          -- 'SMART' (IBKR chose venue) | pinned exchange
    strategy_id        Nullable(UUID),                  -- FK book.strategies; populated by trade engine
    -- security link
    listing_id         Nullable(UUID),
    security_id        Nullable(UUID),
    contract_id        Nullable(UUID),
    product_type       LowCardinality(String),
    vendor_id          String,
    trading_symbol     String,
    currency           LowCardinality(String),
    -- fill
    exec_time          DateTime64(3, 'UTC'),
    side               LowCardinality(String),          -- 'BUY'|'SELL'|'SSHORT'
    quantity           Decimal(20,6),
    price              Decimal(20,6),
    exchange           LowCardinality(String),          -- ACTUAL fill venue (ARCA, NASDAQ, IEX, ...)
    liquidity_flag     LowCardinality(String),          -- 'ADDED'|'REMOVED'|'ROUTED'|''
    -- commission + PnL
    commission         Nullable(Decimal(18,6)),
    commission_ccy     LowCardinality(String),
    realized_pnl       Nullable(Decimal(20,6)),
    -- resolution audit
    resolution_confidence Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    -- provenance + PIT
    source, source_channel, raw_id, ingest_run_id,
    as_of_time, ingested_at, version,
    -- skip index: perm_id joins from the trade engine
    INDEX idx_perm_id       perm_id       TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_listing_id    listing_id    TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(exec_time))
ORDER BY (broker_code, account_id, exec_time, exec_id);
```

**Idempotency:** `exec_id` is broker-immutable — re-pulling the same window is a
no-op post-merge. Ingest scripts always pull with overlap; ReplacingMergeTree
deduplicates.

**Join to trade engine intent:** the trade engine's own `proposed_orders` table
(in its own schema) references `broker.executions` via `perm_id`. FactorLab is
not responsible for that join; the engine does its own attribution using this
table as the fill oracle.

### 11.4 `broker.open_orders_snapshot`

Time series of unfilled orders. Observed, not owned — the trade engine places;
we mirror so PnL reconstruction can see intent alongside fills.

```sql
CREATE TABLE broker.open_orders_snapshot (
    snapshot_time      DateTime64(3, 'UTC'),
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    perm_id            Int64,                           -- durable join key
    order_id           Int64,
    placed_by_client   Nullable(Int32),
    -- origin (added rev 5) — mirrors broker.executions columns for consistency
    execution_method_id LowCardinality(String),         -- FK ref.execution_methods
    order_ref          Nullable(String),
    strategy_id        Nullable(UUID),                  -- FK book.strategies
    -- security link
    listing_id         Nullable(UUID),
    security_id        Nullable(UUID),
    contract_id        Nullable(UUID),
    product_type       LowCardinality(String),
    vendor_id          String,
    trading_symbol     String,
    currency           LowCardinality(String),
    -- order
    side               LowCardinality(String),
    order_type         LowCardinality(String),          -- 'LMT','MKT','MOC','MOO','LOC','LOO','STP','STPLMT',...
    time_in_force      LowCardinality(String),          -- 'DAY','GTC','IOC','FOK',...
    quantity           Decimal(20,6),
    filled_quantity    Decimal(20,6),
    remaining_quantity Decimal(20,6),
    limit_price        Nullable(Decimal(20,6)),
    aux_price          Nullable(Decimal(20,6)),
    status             LowCardinality(String),          -- 'PreSubmitted','Submitted','Cancelled','Filled','Inactive',...
    -- resolution audit
    resolution_confidence Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    -- provenance + PIT
    source, source_channel, raw_id, ingest_run_id,
    as_of_time, ingested_at, version,
    INDEX idx_perm_id       perm_id       TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(snapshot_time))
ORDER BY (broker_code, account_id, snapshot_time, perm_id);
```

### 11.5 Materialized helpers

```sql
-- Latest position per (broker, account, security) — for "current portfolio" dashboards
CREATE MATERIALIZED VIEW broker.positions_latest TO broker.positions_latest_storage AS
SELECT
    broker_code, account_id, account_mode,
    coalesce(listing_id, toUUID('00000000-0000-0000-0000-000000000000')) AS listing_id_key,
    vendor_id,
    argMax(position,       snapshot_time) AS position,
    argMax(avg_cost,       snapshot_time) AS avg_cost,
    argMax(market_price,   snapshot_time) AS market_price,
    argMax(market_value,   snapshot_time) AS market_value,
    argMax(unrealized_pnl, snapshot_time) AS unrealized_pnl,
    argMax(trading_symbol, snapshot_time) AS trading_symbol,
    argMax(product_type,   snapshot_time) AS product_type,
    max(snapshot_time)                     AS as_of
FROM broker.positions_snapshot
GROUP BY broker_code, account_id, account_mode, listing_id_key, vendor_id;
```

```sql
-- Daily NAV per (broker, account) in BASE currency — the equity-curve source.
-- Filters on metric_canonical (added rev 11 per F10) not vendor-native metric,
-- so a second broker (Schwab, ...) with a different vendor tag for the same
-- concept works uniformly via ref.broker_metrics_map.
CREATE MATERIALIZED VIEW broker.nav_daily TO broker.nav_daily_storage AS
SELECT
    broker_code, account_id, account_mode,
    toDate(snapshot_time) AS trade_date,
    argMax(value_num, snapshot_time) AS nav
FROM broker.account_state_snapshot
WHERE metric_canonical = 'net_liquidation' AND segment = '' AND currency = 'BASE'
GROUP BY broker_code, account_id, account_mode, trade_date;
```

`derived.forward_test_attribution` (built in Wave 6+) joins `broker.nav_daily`
(paper, per strategy) against `derived.backtest_returns` to score forward-test
PnL vs backtest predictions.

```sql
-- Daily denormalized margin state (added rev 5) — "current margin state" is
-- one row per (broker, account, trade_date) instead of ~10 rows filtered from
-- broker.account_state_snapshot. Populated from account_state_snapshot's
-- ~15-20 margin-relevant tags. Filters on metric_canonical (rev 11 per F10)
-- so the same MV DDL works across brokers via ref.broker_metrics_map.
CREATE MATERIALIZED VIEW broker.margin_state_daily TO broker.margin_state_daily_storage AS
SELECT
    broker_code, account_id, account_mode,
    toDate(snapshot_time) AS trade_date,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='net_liquidation')       AS nav,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='total_cash_value')      AS cash,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='maint_margin_req')      AS maint_margin_req,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='init_margin_req')       AS init_margin_req,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='full_maint_margin_req') AS full_maint_margin_req,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='full_init_margin_req')  AS full_init_margin_req,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='excess_liquidity')      AS excess_liquidity,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='cushion')               AS cushion,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='available_funds')       AS available_funds,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='buying_power')          AS buying_power,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='gross_position_value')  AS gross_position_value,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='leverage')              AS leverage,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='look_ahead_maint')      AS look_ahead_maint,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='look_ahead_init')       AS look_ahead_init,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='reg_t_margin')          AS reg_t_margin,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='reg_t_equity')          AS reg_t_equity,
    argMax(value_num, snapshot_time) FILTER (WHERE metric_canonical='sma')                   AS sma,
    argMax(currency,  snapshot_time) FILTER (WHERE metric_canonical='net_liquidation')       AS base_currency,
    max(snapshot_time)                                                                       AS last_snapshot
FROM broker.account_state_snapshot
WHERE segment = ''
GROUP BY broker_code, account_id, account_mode, trade_date;
```

Alerting rule (downstream): `cushion < 0.15` or `excess_liquidity < 0` fires a margin-warn / margin-call alert to the trade engine.

### 11.6 Multi-account, multi-broker semantics

Every query filters on `(broker_code, account_id, account_mode)` — never mix
silently. A few canonical patterns:

```sql
-- Paper portfolio as of yesterday's close
SELECT trading_symbol, position, market_value
FROM broker.positions_snapshot
WHERE broker_code='ibkr' AND account_mode='paper'
  AND snapshot_time = (
      SELECT max(snapshot_time)
      FROM broker.positions_snapshot
      WHERE broker_code='ibkr' AND account_mode='paper'
        AND snapshot_time < today()
  );

-- Live NAV equity curve, trailing 12 months
SELECT trade_date, nav
FROM broker.nav_daily
WHERE broker_code='ibkr' AND account_mode='live'
  AND trade_date >= today() - 365
ORDER BY trade_date;

-- Forward-test divergence (paper - backtest)
SELECT p.trade_date,
       p.paper_return,
       b.backtest_return,
       p.paper_return - b.backtest_return AS divergence
FROM (
    SELECT trade_date,
           (nav / lagInFrame(nav) OVER (ORDER BY trade_date)) - 1 AS paper_return
    FROM broker.nav_daily
    WHERE broker_code='ibkr' AND account_mode='paper' AND account_id='DUE375963'
) p
JOIN derived.backtest_returns b USING (trade_date)
WHERE b.strategy_id = {strategy_uuid:UUID};
```

### 11.7 Ingest, resolution, reconciliation

| Job | Cadence | Writes |
|---|---|---|
| Position snapshot (both accounts) | 06:00 ET + 16:30 ET | `broker.positions_snapshot` |
| Account state snapshot (both accounts) | 06:00 ET + 16:30 ET | `broker.account_state_snapshot` |
| Executions pull (both accounts) | Every 15 min during session | `broker.executions` |
| Open orders snapshot (both accounts) | Every 5 min during session | `broker.open_orders_snapshot` |

Each job iterates `(broker_code='ibkr', account_mode) ∈ {paper, live}` and
connects to the appropriate Gateway. Two Gateway instances run concurrently on
different ports (see [`docs/data-sources/06-ibkr.md`](../data-sources/06-ibkr.md)).

**Resolution flow:** on ingest, look up `vendor_id` (IBKR conid) in
`ref.identifier_aliases`. On hit: populate `listing_id`/`security_id`/`entity_id`
and set `resolution_confidence='canonical'`. On miss: leave FK nullable, set
`resolution_confidence='vendor_only'`, and enqueue to `meta.unresolved_entities`.
The resolver's next pass writes a new row (later `as_of_time`) with canonical
IDs filled in — the old row stays for point-in-time consistency, the new row
becomes latest for `positions_latest`.

**Reconciliation contract:** morning position snapshot is compared to prior EOD
snapshot per `(broker_code, account_id)`. Any drift writes a row to
`meta.reconciliation_drift` (extension of `meta.*`, defined in Wave 7). The trade
engine consumes this table and refuses to start if unresolved drift exists.
**FactorLab detects; trade engine acts.**

### 11.8 Extending to another broker

Adding a second broker (e.g. Schwab as a portfolio source) is a row addition:
`broker_code='schwab'`, `account_mode='live'`, dispatch to a Schwab-specific
ingester that writes the same four tables. No new tables. No schema migration.
Cross-broker rollups (`SELECT sum(nav) FROM broker.nav_daily GROUP BY broker_code`)
work uniformly. Note: `broker_code` and `source` share a value namespace but
sit in different tables — `source='schwab'` in `market.bars` is the Schwab
market-data adapter; `broker_code='schwab'` in `broker.*` is the Schwab broker
integration. Same firm, different role — the schema (`market.*` vs `broker.*`)
disambiguates.

### 11.9 `broker.cash_flows`

**Why this exists**: NAV changes for reasons other than trades and price moves — dividends arrive, cash accrues interest, deposits/withdrawals happen, IBKR auto-converts currencies, tax gets withheld on foreign dividends. Without capturing these, every dividend arrival looks like unexplained NAV drift and reconciliation becomes noisy-and-eventually-meaningless. IBKR exposes cash flows via `ib.flexReport()` (batch, T+1) and via `accountUpdates` real-time cashUpdate events. This table is the destination for both paths.

```sql
CREATE TABLE broker.cash_flows (
    event_time         DateTime64(3, 'UTC'),        -- when the cash event occurred (broker-reported)
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    -- classification
    cash_flow_type     LowCardinality(String),      -- 'dividend','interest','deposit','withdrawal',
                                                     --   'fx_conversion','commission','tax_withholding',
                                                     --   'fee','corporate_action_cash','sweep','other'
    amount             Decimal(20,6),                -- signed; positive = credit, negative = debit
    currency           FixedString(3),
    -- linkage
    security_id        Nullable(UUID),               -- for dividends/corporate-action cash
    listing_id         Nullable(UUID),
    contract_id        Nullable(UUID),
    strategy_id        Nullable(UUID),               -- FK book.strategies; populated when attributable
    trade_name_id      Nullable(UUID),               -- FK book.trade_names — added rev 11 (F8);
                                                     --   populated only for cash_flow_type IN
                                                     --   ('commission','fx_conversion') via the
                                                     --   linked_exec_id → book.attribution.trade_name_id
                                                     --   join; NULL for dividends/interest/deposits/
                                                     --   withdrawals (those grains are theme/strategy/
                                                     --   account, not trade_name — commission-per-
                                                     --   trade_name is the specific analysis this
                                                     --   column unlocks: post-trade cost per named bet)
    linked_exec_id     Nullable(String),             -- when tied to a specific fill (commission)
    linked_corp_action_id Nullable(UUID),            -- when tied to a corporate action (dividend, tender)
    -- description
    description        String,                        -- IBKR-provided memo
    ex_date            Nullable(Date),                -- for dividends
    pay_date           Nullable(Date),                -- for dividends
    -- resolution audit
    resolution_confidence Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    -- provenance + PIT
    source             LowCardinality(String),        -- 'ibkr'
    source_channel     LowCardinality(String),        -- 'flex_report_cash','account_updates_stream'
    raw_id             Nullable(UUID),
    ingest_run_id      UUID,
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    version            UInt64,
    -- dedup key (added below)
    INDEX idx_security_id security_id TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(event_time))
ORDER BY (broker_code, account_id, event_time, cash_flow_type,
          coalesce(security_id, toUUID('00000000-0000-0000-0000-000000000000')),
          amount, description);
```

**Dedup**: IBKR's Flex report emits stable transaction IDs — if available, prepend one to the ORDER BY for exact idempotency. Without it, the composite ORDER BY tuple is unique enough for daily-run idempotency (dividend on same ex_date for same security is one event).

**Downstream use**: `broker.nav_daily` is enriched by joining `broker.cash_flows` — daily attribution ("today's NAV moved +X, of which +Y from dividends, +Z from mtm, -W from trades").

### 11.10 `broker.applied_corporate_actions`

**Why this exists**: `ref.corporate_actions` records that URNU had a 2:1 split on some date. But *"did that action hit MY portfolio, and by how much?"* is not derivable from that dim alone — depends on whether the account held the security on the record date. Without this table, post-split position count doubling looks like a mystery position adjustment and reconciliation (§11.7) can't distinguish "corporate action" from "actual drift." This is the link between global corp-action events and per-account impact.

```sql
CREATE TABLE broker.applied_corporate_actions (
    event_time         DateTime64(3, 'UTC'),        -- when the action applied to this account
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    -- linkage
    corp_action_id     UUID,                         -- FK ref.corporate_actions (the global event)
    action_type        LowCardinality(String),       -- 'split','reverse_split','dividend','spinoff',
                                                      --   'merger','tender','symbol_change','delisting'
    security_id        UUID,                         -- security affected in this account
    listing_id         Nullable(UUID),
    -- impact
    position_before    Decimal(20,6),
    position_after     Decimal(20,6),
    cash_impact        Decimal(20,6),                -- signed
    cash_currency      FixedString(3),
    ratio_num          Nullable(Decimal(18,6)),      -- 2 for 2:1 split
    ratio_den          Nullable(Decimal(18,6)),      -- 1 for 2:1 split
    -- description
    description        String,
    -- reconciliation aid: reconciler consults this table before writing meta.reconciliation_drift
    triggers_expected_drift Bool,                    -- true → next snapshot delta is EXPECTED
    -- provenance + PIT
    source             LowCardinality(String),        -- 'ibkr' | 'ref.corporate_actions'
    source_channel     LowCardinality(String),
    raw_id             Nullable(UUID),
    ingest_run_id      UUID,
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    version            UInt64,
    -- Skip index (added rev 11 per F11): meta.reconciliation_drift consults this
    -- table with a WHERE (broker_code, account_id, security_id, event_time) filter;
    -- security_id sits at position 4 in the sort key, so without a skip index the
    -- reconciler scans the (broker, account, event_time) range for every security.
    INDEX idx_security_id security_id TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(event_time))
ORDER BY (broker_code, account_id, event_time, security_id, corp_action_id);
```

Populated by cross-referencing `broker.positions_snapshot` deltas against `ref.corporate_actions` on each morning snapshot cycle. Corporate actions that DIDN'T hit the account (no holding on record date) do not create rows here — this table is per-account impact only.

### 11.11 `broker.order_events`

**Why this exists**: `broker.executions` captures fills only. `broker.open_orders_snapshot` is a 5-min photograph that loses everything between snapshots. Neither table lets you answer *"what was the original limit price before we modified it?"* or *"how long did the order sit before filling?"* or *"why did the exchange reject it?"* Order-lifecycle events — placed / accepted / modified / partially-filled / cancelled / rejected — are the raw material for slippage decomposition, latency profiling, execution-quality analysis, and backtest-vs-live cost-model calibration. `ib_async` exposes them for free via `Trade.log`; this table persists them.

```sql
CREATE TABLE broker.order_events (
    event_time         DateTime64(3, 'UTC'),
    broker_code        LowCardinality(String),
    account_id         String,
    account_mode       LowCardinality(String),
    country_code       FixedString(2),
    -- order identity
    perm_id            Int64,                        -- durable order ID (join key to executions/open_orders)
    order_id           Int64,                        -- session-scoped
    event_seq          UInt32,                       -- ordinal within perm_id (0-indexed)
    -- what happened
    event_type         LowCardinality(String),       -- 'submitted_to_broker','ack_from_broker',
                                                      --   'accepted_by_exchange','modified',
                                                      --   'partial_fill','filled',
                                                      --   'cancelled_by_api','cancelled_by_exchange',
                                                      --   'rejected','expired','inactive'
    prior_state        Nullable(LowCardinality(String)),
    new_state          LowCardinality(String),       -- IBKR status enum:
                                                      --   'PendingSubmit','PreSubmitted','Submitted',
                                                      --   'ApiPending','PendingCancel','ApiCancelled',
                                                      --   'Cancelled','Filled','Inactive','PartiallyFilled'
    -- state deltas (only populated when the event modified them)
    limit_price_before Nullable(Decimal(20,6)),
    limit_price_after  Nullable(Decimal(20,6)),
    aux_price_before   Nullable(Decimal(20,6)),
    aux_price_after    Nullable(Decimal(20,6)),
    quantity_before    Nullable(Decimal(20,6)),
    quantity_after     Nullable(Decimal(20,6)),
    -- fill state at event time (running totals — snapshot, not delta)
    filled_qty_at_event      Nullable(Decimal(20,6)),
    avg_fill_price_at_event  Nullable(Decimal(20,6)),
    remaining_at_event       Nullable(Decimal(20,6)),
    -- routing
    route_before       Nullable(LowCardinality(String)),   -- 'SMART' | pinned exchange
    route_after        Nullable(LowCardinality(String)),
    -- broker feedback (populated on rejection / warning / cancel-reason)
    message            Nullable(String),              -- IBKR-provided text, e.g. "Order too aggressive"
    error_code         Nullable(Int32),
    warning_text       Nullable(String),
    -- security link (denormalized for query-time filter without joining executions)
    listing_id         Nullable(UUID),
    security_id        Nullable(UUID),
    contract_id        Nullable(UUID),
    product_type       LowCardinality(String),
    vendor_id          String,
    trading_symbol     String,
    currency           LowCardinality(String),
    -- origin (mirrors executions / open_orders)
    execution_method_id LowCardinality(String),      -- FK ref.execution_methods
    order_ref          Nullable(String),
    strategy_id        Nullable(UUID),               -- FK book.strategies
    placed_by_client   Nullable(Int32),
    -- resolution audit
    resolution_confidence Enum8('exact'=1,'high'=2,'medium'=3,'low'=4,'unresolved'=5,'manual_override'=6),
    -- provenance + PIT
    source             LowCardinality(String),        -- 'ibkr'
    source_channel     LowCardinality(String),        -- 'trade_log_stream','trade_log_batch_pull'
    raw_id             Nullable(UUID),
    ingest_run_id      UUID,
    as_of_time         DateTime64(3, 'UTC'),
    ingested_at        DateTime64(3, 'UTC'),
    version            UInt64,
    -- skip indices
    INDEX idx_strategy_id   strategy_id  TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_listing_id    listing_id   TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_error_code    error_code   TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(event_time))
ORDER BY (broker_code, account_id, perm_id, event_seq);
```

**Idempotency**: `(broker_code, account_id, perm_id, event_seq)` in the ORDER BY guarantees dedup on re-pull. `event_seq` comes from ib_async's `Trade.log` list index — stable per order.

**Ingest paths** (either works, tradeoff is latency vs simplicity):
- **Stream** — subscribe to `Trade.filledEvent`, `Trade.statusEvent`, `Trade.modifyEvent`, `Trade.cancelEvent` on a persistent connection; write rows as events fire. Sub-second capture, but needs the process to stay up.
- **Batch pull** — piggyback on the 5-min open-orders snapshot: for every Trade currently in `ib.trades()`, read `Trade.log` fully; upsert into `broker.order_events` (idempotent by event_seq). Simpler to fit our existing snapshot architecture. Terminal-state orders drop out of `ib.trades()` after Gateway restart — flush before restart or accept the small blind spot.

**Downstream materialized views** (built as needed, not Wave 7):
- `broker.order_latency_daily` — placed→ack, placed→first-fill, placed→terminal, per order and per strategy
- `broker.execution_quality_daily` — fill rate, average slippage vs initial limit, cancel rate, reject rate — per strategy and per venue
- Slippage attribution — decomposition per fill into time-drift / market-impact / modification-cost

**Why relatively cheap despite verbose event stream**: at your scale (dozens to hundreds of orders/day, ~10-30 events per order), this is thousands of rows/day at most. Compared to `market.bars` at millions/day, it's a rounding error. LowCardinality columns compress hard.

### 11.12 Book-namespace extensions (rev 7)

The following existing `broker.*` tables gain columns to link broker-side truth to
the book layer (`book.trade_names`, `book.themes`). This is a pure
additive change — no reshape, no data loss. `theme_id` is denormalized on
`positions_snapshot` only (the hottest read path for a PM dashboard: "show me
current exposure by theme"). Elsewhere, `trade_name_id → theme_id` derives via a
single join to `book.trade_names`.

| Table | Added columns | Rationale |
|---|---|---|
| `broker.positions_snapshot` | `trade_name_id Nullable(UUID)`, `theme_id Nullable(UUID)` | Live-position dashboard groups by theme without a join. `theme_id` is denorm — recomputed on each snapshot from `book.trade_names.theme_id`. |
| `broker.executions` | `trade_name_id Nullable(UUID)` | Trade_name-level PnL slicing at fill granularity. `strategy_id` already exists per rev 5. |
| `broker.open_orders_snapshot` | `trade_name_id Nullable(UUID)` | Working orders visible under their parent trade_name in the PM dashboard. |
| `broker.order_events` | (already present per rev 5 spec: `strategy_id`) — add `trade_name_id Nullable(UUID)` | Event lifecycle attributable to a trade_name for latency/slippage-by-trade_name analysis. |
| `broker.cash_flows` | `trade_name_id Nullable(UUID)` — added rev 11 (F8) | Commission-per-trade_name post-trade cost analysis. Populated only for `cash_flow_type IN ('commission','fx_conversion')` via the `linked_exec_id → book.attribution.trade_name_id` join. NULL for dividends/interest/deposits/withdrawals — those grains are correctly theme/strategy/account, not trade_name. |

**Population**: `trade_name_id` on all five tables is populated at ingest by joining
`book.attribution` on `execution_id` (executions/order_events/commission
cash_flows) or by consulting the active-trade_leg for the account+listing
(positions_snapshot, open_orders_snapshot). Unassigned fills leave
`trade_name_id NULL` and surface on the attribution-coverage dashboard.

**Not extended**: `broker.applied_corporate_actions`, `broker.account_state_snapshot`.
Applied corporate actions are per-account not per-trade_name. Account state is
fund-wide. `broker.cash_flows` was previously in this list — rev 11 (F8) split
it out because commission/fx-conversion rows benefit from trade_name grain
even though dividend/interest rows don't.

### 11.13 Book-namespace rollup MVs

The fund-manager-facing surfaces. One query gets "how is the AI theme doing this
week" or "what's my current gross exposure per trade_name." Populated as
`MATERIALIZED VIEW ... TO ..._storage AS SELECT ...` incrementally on
`broker.positions_snapshot` and `broker.executions` inserts.

| MV | Grain | Reads from | Answers |
|---|---|---|---|
| `broker.exposure_by_theme_daily` | `(broker_code, account_mode, theme_id, trade_date)` | `broker.positions_snapshot` EOD row per position | Gross / net / long / short exposure per theme in base ccy and USD; count of positions |
| `broker.exposure_by_trade_name_daily` | `(broker_code, account_mode, trade_name_id, trade_date)` | Same | Same metrics per trade_name; leg-level detail sits in `positions_snapshot` filtered by `trade_name_id` |
| `broker.pnl_by_theme_daily` | `(broker_code, account_mode, theme_id, trade_date)` | `broker.positions_snapshot` (unrealized) + `broker.executions` (realized, via `book.attribution`) | Daily / MTD / ITD realized + unrealized PnL per theme |
| `broker.pnl_by_trade_name_daily` | `(broker_code, account_mode, trade_name_id, trade_date)` | Same | Per-trade_name PnL — the trade-thesis scorecard |

**Engine**: `SummingMergeTree` for exposure (additive metrics only) or
`AggregatingMergeTree` with `sumState`/`argMaxState` for PnL (need last-observed
unrealized within the day). Full DDL deferred until Wave 8 implementation — the
grain and read source are the contract.

**Alerting hooks**: `pnl_by_trade_name_daily.mtd_pnl_pct < -stop_loss_pct` (from
`risk.budgets` where `scope='trade_name'`) fires a trade_name-level stop-loss
alert. Same pattern at theme scope for drawdown alerts.

### 11.14 `research.owned_listings` view (rev 11 per F13)

Research-time helper for joins against alt-data facts that key on
`listing_id`. Wraps `broker.positions_snapshot` with a filter for
resolved, held, live positions and takes the latest snapshot per listing.

```sql
CREATE VIEW research.owned_listings AS
SELECT
    listing_id,
    argMax(security_id,    snapshot_time) AS security_id,
    argMax(entity_id,      snapshot_time) AS entity_id,
    argMax(trading_symbol, snapshot_time) AS trading_symbol,
    argMax(position,       snapshot_time) AS position,
    argMax(market_value,   snapshot_time) AS market_value,
    max(snapshot_time)                    AS as_of
FROM broker.positions_snapshot
WHERE listing_id IS NOT NULL           -- exclude unresolved positions
  AND account_mode = 'live'            -- research asks about the real book
  AND position != 0                    -- exclude closed positions
GROUP BY listing_id;
```

**Why**: query-walk #4 in §19.7 — "which senator's PTRs from 2025 hit tickers
I now own?" — joins `alt.political_trades.listing_id` to
`broker.positions_snapshot.listing_id`. The raw table has
`Nullable(UUID)` on `listing_id` because the resolver may miss;
joining raw introduces a silent completeness gap ("18% of my positions have
no matching PTR" could mean coverage gap OR resolution failure — the
researcher can't tell). This view makes the semantics explicit: rows here
have a resolved listing and are actually held, so an empty join
unambiguously means PTR coverage gap.

**Not built until Wave 7 tier 3** (broker cutover to `positions_latest`);
schema-only declaration here reserves the name.

---

## 12. `raw` — Immutable archive

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

## 13. Cross-cutting concerns

### 13.1 Multi-vendor conflict resolution

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

### 13.2 Time zones

- **Storage:** everything in `market.*`, `fundamentals.*`, `alt.*`, `meta.*` is UTC.
- **Bar time:** UTC always. Daily bars use `00:00:00 UTC of trade_date`.
- **Trade date:** session-local calendar date (e.g., 2026-09-18 for a NYSE session even if UTC crosses).
- **Session windows:** stored in `ref.sessions` as local-time strings; resolved to UTC at query time using `ref.holidays` for adjustments.

### 13.3 Corporate actions

- Raw prices in `market.bars` are **never** adjusted post-hoc.
- Split/dividend arrives → new row in `ref.corporate_actions` → nightly job
  recomputes `ref.adjustment_factors` → `market.bars_adjusted` MV serves
  research.
- Every research query defaults to reading `bars_adjusted`. Execution reads
  `bars` (raw) because live trading uses live prices.

### 13.4 PIT correctness — bitemporal, enforced

Two time axes on every fact row:
- **valid_time** = `event_time`, `bar_time`, `transaction_date`, `period_end`
  (when the fact was true in the world)
- **transaction_time** = `as_of_time`, `filed_at`, `ingested_at`
  (when the fact was knowable to us)

Backtests must filter on **transaction_time** to avoid look-ahead bias.
Enforcement is by construction — not convention. Four defenses stack:

#### 13.4.1 Grant separation

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

#### 13.4.2 The `research.*` schema — parametric wrappers

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

#### 13.4.3 CI enforcement

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

#### 13.4.4 PR review template (`.github/pull_request_template.md`)

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

#### 13.4.5 What NOT to enforce

- Ad-hoc research notebooks in `playground/` are exempted from the CI check
  (developers explore raw data there). They also cannot ship to production —
  the CI check runs on `src/`, `scripts/`, `migrations/`, and `notebooks/`
  reachable from the PR.
- Ops dashboards and admin scripts have their own path exemption because
  they legitimately query raw ingestion state for health monitoring.

### 13.5 Symbol reassignment (FB → META)

`ref.identifier_aliases` is SCD-2. A ticker change:
1. `ref.identifier_aliases`: existing (`ticker`,`META`,US)→(entity_facebook) gets `valid_to = 2022-06-08`
2. New row: (`ticker`,`META`,US)→(entity_facebook) with `valid_from = 2022-06-09`
3. Old ticker gets a `ref.corporate_actions` row `action_type='ticker_change'`
4. Existing `listing_id` is unchanged; `ref.listings.trading_symbol` becomes `META` (SCD-2 on that column would be better; open question)

### 13.6 Delisting and survivorship bias

- Delisted listings get `ref.listings.active = false`, `last_traded = <date>`.
- `ref.universe_membership` records the delisting via `effective_to` and
  `reason='delisting'`.
- Backtests reading historical universe include delisted names — no survivorship bias.

### 13.7 Universe reconstruction

To reconstruct R3K as of 2024-03-15:
```sql
SELECT listing_id
FROM ref.universe_membership
WHERE universe_id = 'r3k'
  AND effective_from <= '2024-03-15'
  AND (effective_to IS NULL OR effective_to > '2024-03-15');
```
1 query, 1 table. No joins.

### 13.8 FX conversion at scale

Cross-country research (INR-listed vs USD-listed) needs USD-normalized
returns. Options:
1. Compute USD notional per bar on read (join to `market.fx_rates`)
2. Add `notional_usd` MV over `market.bars × market.fx_rates` (chosen)

MV convention: FX at `bar_time` close, `fix_convention='close'`. For research
that needs a different fix (WM/Reuters 4pm London), parameterize by rebuilding
MV with different fix_convention.

### 13.9 Entity resolution failure policy

Alt-data ingesters try to resolve `ticker` → `listing_id` via
`ref.identifier_aliases`. Two failure modes:
- **Not found**: row lands with `listing_id = NULL`, `resolution_confidence =
  'unresolved'`. Goes to a review queue (`meta.unresolved_entities`).
- **Ambiguous**: multiple candidate listings. Row lands with best guess,
  `resolution_confidence = 'medium'` or `'low'`. Reviewer promotes to
  `'manual_override'`.

Never silently drop rows. Never guess without recording confidence.

### 13.10 Restatement handling

Fundamentals get restated. Q1 2024 revenue announced 2024-04-15 as $X; amended
2025-02-10 as $Y.

Handled by `fundamentals.line_items` version bumps: each restatement is a new
row with same (`entity_id`, `tag`, `period_end`) but new `filing_id`, new
`filed_at`, new `as_of_time`. Original row is not overwritten.

Research PIT snapshot MV uses `argMax(value, filed_at) FILTER (filed_at <=
<backtest_date>)` → automatically gets the view that was current on that date.

### 13.11 Canonical FK conventions

ClickHouse doesn't enforce foreign-key integrity at the engine level.
Structural enforcement stacks:

**The four canonical FactorLab UUIDs.** Every fact row uses these — never
vendor-native identifiers, never raw strings.

| Canonical ID | Lives in | Points to | Denormalized on facts |
|---|---|---|---|
| `entity_id` | `ref.entities` | Issuer / company / individual entity (LEI-keyed where possible) | Yes — enables cross-source joins |
| `security_id` | `ref.securities` | Security-level (ISIN-keyed; ADR + common are separate) | Yes |
| `listing_id` | `ref.listings` | Venue-scoped tradable (exchange × symbol) — **primary key for market data** | Yes |
| `contract_id` | `ref.contracts` | Derivative contract (futures + options) | Yes for derivatives only |

Plus one **person canonical**:

| Canonical ID | Lives in | Points to | Notes |
|---|---|---|---|
| `legislator_entity_id` | `ref.entities` | Individual legislator (`entity_type='person_legislator'`) | Same UUID space as issuer entities — different `entity_type` |

Plus **book canonicals** (all originate in `book.*`; added rev 5 and refactored rev 7):

| Canonical ID | Lives in | Points to | Notes |
|---|---|---|---|
| `strategy_id` | `book.strategies` | A repeatable procedure that generates trade_names (rev 5; moved from `derived.strategies` in rev 7) | Denormalized on `broker.executions`, `broker.open_orders_snapshot`, `broker.order_events`, `broker.cash_flows`, `derived.forward_test_attribution` |
| `theme_id` | `book.themes` | An investment thesis / thematic bucket (rev 6; namespace changed rev 7) | Denormalized on `broker.positions_snapshot` (hot read path); derives via `trade_name_id → book.trade_names.theme_id` elsewhere |
| `trade_name_id` | `book.trade_names` | A named investment expression under a theme — the unit of investment thesis (rev 6 as `trade_id`; renamed rev 7) | Denormalized on `broker.positions_snapshot`, `broker.executions`, `broker.open_orders_snapshot`, `broker.order_events`; bridge lives in `book.attribution` |

Resolver-only-writes rule (defense 1) applies to these UUIDs equally: no
ingester composes a `strategy_id`, `theme_id`, or `trade_name_id` — they are
looked up or `NULL`. `book.attribution` is the single write path for
`trade_name_id` on `broker.*` fact rows.

**FK integrity enforcement (four defenses):**

1. **Resolver-only writes.** Every ingester writes canonical IDs only after
   `resolve_to_*()` call. No ingester composes UUIDs itself. Resolution failures
   land as `NULL` FK with `resolution_confidence='unresolved'` — never fabricated.
2. **Nightly integrity job** (`meta.integrity_checks`) — scans facts for
   dangling FKs (row present in fact, target missing in dim). Alerts if
   dangling-FK rate exceeds threshold per dataset.
3. **CI schema conformance tests** — every migration asserts FK columns exist
   with correct type; test suite includes `assert_no_dangling_fks(dataset)` for
   representative samples.
4. **Code review checklist** — every ingester PR must show which
   `resolve_to_*()` function it calls, and how it handles unresolved returns.

**Vendor native IDs live in aliases, not on facts.** A `schwab_conid`,
`ibkr_conid`, `eodhd_symbol`, `upstox_instrument_key`, `bioguide_id`,
`fec_candidate_id`, `cik`, `cusip`, `isin`, `figi` — all land in
`ref.identifier_aliases` with `target_id` pointing to the canonical UUID.
Facts hold canonical IDs; provenance strings (e.g., `ticker_raw`,
`legislator_name_raw`) are marked with `_raw` suffix so it's obvious at a
glance that they are not for joining.

**5NF adherence check (informal):** for any fact table, ask "is any column
here derivable from another column via a dim lookup?" If yes, it's a
denormalization — must be justified (partition prune, join accelerant, or
fact-scoped truth). All 5NF violations are documented in each table's schema
comment. No silent duplication.

### 13.12 Denormalization rule: IDs only, not attributes

Bridge and fact tables denormalize *only* the canonical UUIDs (`theme_id`,
`strategy_id`, `trade_name_id`, `security_id`, `entity_id`, `listing_id`,
`contract_id`) — never mutable attributes like `priority`, `base_currency`,
`owner_entity_id`, or classification tags. Attribute changes on the dim table
then don't require rewriting fact history: bump a `book.trade_names.priority`
from `medium` to `high` and every historical `book.attribution` row stays put,
correctly reflecting the priority *at the time the fact was written* by
joining to `book.trade_names` at query time. If we had denormalized `priority`
onto every `book.attribution` row, that same edit would either (a) leave
historical rows stale (correct-at-write, wrong-at-read for dashboards that
want "current priority"), or (b) require rewriting every historical row (a
data-integrity minefield across dozens of MVs and a lineage nightmare).

**Cost of this rule**: dashboard queries join the dim to fetch attributes.
For hot paths (PM dashboard, alert engine) that cost is measured — if it's
material we build a covering MV that joins dim+fact at write time.

**Benefit**: one source of truth for every dim attribute. Zero denorm-drift
audit burden. `book.attribution` today (as verified in the rev-8 §7.7
audit) complies: it denorms `theme_id` and `strategy_id` (both canonical
UUIDs from `book.trade_names`) but denorms none of `trade_names.priority`,
`trade_names.base_currency`, or `themes.owner_entity_id` — those are joined
on read. This rule (added rev 11 per F7) documents that as policy going
forward.

---

## 14. Testing and code review discipline

Not a nice-to-have. This layer is what makes the schema *actually* deliver
its 5NF and PIT guarantees. Every layer of the stack has a matching test
category, coverage target, and CI gate.

### 14.1 Test categories

| Category | Purpose | Runs on | Coverage target |
|---|---|---|---|
| **Unit** | Pure-function correctness (parsers, resolvers, normalizers) | Every commit | ≥90% line coverage per module |
| **Schema conformance** | Every table has expected columns, types, sort keys, indices | PR + nightly | 100% of production tables |
| **Integrity — dangling FKs** | No fact row references a non-existent dim row | Nightly against prod | Dangling-FK rate <0.5% per dataset |
| **PIT safety** | No research query returns rows with `as_of_time > asof_date` | PR (against staging fixtures) | 100% of `research.*` views |
| **Resolver golden set** | Known (input, expected canonical id) pairs — regression on resolver drift | PR + nightly | ≥100 golden cases per resolver |
| **Ingest end-to-end** | Vendor payload → fact row shape → integrity check passes | PR (with recorded fixtures) | 100% of ingest paths |
| **Coverage / freshness** | Every active source has data landing within its SLA | Continuous (health module) | Alert on breach |
| **Idempotency** | Re-running an ingester over same input produces same result (byte-identical facts) | Nightly | 100% of ingesters |
| **Restatement / SCD-2 semantics** | Amendments produce new rows, don't overwrite; SCD-2 windows don't overlap | Nightly | 100% of SCD-2 tables |
| **Amount / numeric parsing** | Known filing → known amount range | Every PR to political ingester | ≥50 golden cases across brackets |
| **Backtest reproducibility** | Same `(dataset_version, asof_date, code_version)` → byte-identical returns | Weekly | 100% of published backtests |
| **Book + risk PIT + FK** | `book.themes/trade_names/trade_legs/attribution` and `risk.budgets` FK targets exist; SCD-2 windows on `risk.budgets` + `book.trade_legs` don't overlap per `(scope, scope_id)` / `(trade_name_id, listing_id)`; every `broker.executions.trade_name_id` resolves to an active `book.trade_names` at `event_time` | PR + nightly | 100% of book + risk tables |
| **Denorm bidirectional consistency** (F17 rev 11) | `ref.broker_accounts.strategy_ids` (canonical) and `book.strategies.paper_account_ids` / `live_account_ids` (denorm) agree bidirectionally: for every `(account_id, strategy_id)` pair, membership is present on both sides; nightly integrity job flags any asymmetry as a data-quality event | Nightly | 100% of `ref.broker_accounts` × `book.strategies` |

### 14.2 Coverage targets, not "have some tests"

Each ingester has an explicit, tracked SLO:

- **`src/factorlab/sources/upstox/`**: ≥90% line coverage, ≥95% branch for parsers
- **`src/factorlab/sources/schwab/`**: same
- **`src/factorlab/sources/political/`**: ≥95% (the audit found Sev-1 bugs; extra scrutiny justified)
- **`src/factorlab/storage/`**: 100% coverage on write paths; ingesters call these constantly
- **`src/factorlab/resolvers/`**: 100% coverage on resolution functions + golden-set regression

**Fail the build if coverage regresses more than 1% on the changed module.**
Coverage is a rope, not a jail — a big refactor that legitimately drops
coverage 5% points but adds golden tests is fine, but requires reviewer signoff.

### 14.3 Golden sets — the load-bearing test kind

Regressions in resolvers or parsers silently poison downstream data.
Golden sets catch this at PR time:

- **`tests/golden/bioguide/`** — 100+ (raw_name, expected_bioguide_id) pairs including
  the current pathological cases: `"Hon. Scott Scott Franklin"`,
  `"Hon. Richard Dean Dr McCormick"`, `"Hon. April McClain Delaney"`.
- **`tests/golden/amount_parser/`** — 50+ (filing_text, expected_min, expected_max)
  pairs across all seven brackets. Fixes the Sev-1 default-to-smallest-bucket bug
  and prevents regression.
- **`tests/golden/ticker_resolution/`** — 200+ (ticker, country, asof_date, expected_listing_id) pairs
  covering: exact match, class shares (BRK.A vs BRK.B), ticker reuse (FB→META),
  ADR vs primary listing (`NOK` vs `NOKIA.HE`), delisted names.
- **`tests/golden/occ_option_parser/`** — 30+ (occ_symbol, expected_underlying_listing_id,
  expected_expiry, expected_strike, expected_right) pairs.
- **`tests/golden/security_type/`** — 100+ (raw_ticker, raw_name, filing_asset_type_code,
  expected_security_type) pairs covering the mistags found in the audit:
  `('BILL', 'U.S. Treasury', 'GS') → 'treasury'`,
  `('S', 'Oaktree Strategic Credit Fund', 'OT') → 'mutual_fund'`,
  `('GE', 'GE Aerospace Common Stock', 'OP') → 'common'`.

**Golden files live in the repo, versioned.** When a real edge case is
discovered in prod, the fix PR MUST add a golden test row that would have
caught it. This is enforced by review, not by CI (too language-specific to
regex).

### 14.4 PIT-safety test suite

Every `research.*` view has a paired test:

```python
def test_bars_adjusted_pit_no_leak():
    """research.bars_adjusted must never return rows with as_of_time > asof_date."""
    for asof in [date(2024, 1, 15), date(2024, 6, 30), date(2025, 3, 1)]:
        rows = client.query(
            "SELECT max(as_of_time) FROM research.bars_adjusted(asof_date = %(asof)s)",
            {"asof": asof},
        ).result_rows
        assert rows[0][0] <= datetime.combine(asof, time.max, UTC), \
            f"PIT leak in bars_adjusted at asof={asof}"
```

Runs against staging fixtures on every PR that touches `research.*` schema or
their upstream fact tables.

### 14.5 Integrity + SCD-2 nightly checks

```python
# tests/nightly/test_scd2_no_overlap.py
def test_universe_membership_no_overlap():
    """No two rows for the same (universe, listing) with overlapping [from, to)."""
    conflict_rows = client.query("""
        SELECT universe_id, listing_id, count()
        FROM ref.universe_membership FINAL
        WHERE effective_from <= addDays(effective_to, -1)  -- valid window
        GROUP BY universe_id, listing_id
        HAVING count() > 1
    """).result_rows
    assert not conflict_rows, f"Overlapping SCD-2 windows: {conflict_rows[:5]}"
```

Same pattern for `ref.legislator_terms`, `alt.political_committee_memberships`,
`ref.identifier_aliases`, `ref.listing_migrations`.

### 14.6 Idempotency tests

```python
def test_upstox_ingester_idempotent(recorded_response):
    """Same vendor payload processed twice = same rows (byte-identical)."""
    run1 = ingest_upstox_response(recorded_response, run_id=uuid1())
    run2 = ingest_upstox_response(recorded_response, run_id=uuid2())
    assert run1.rows == run2.rows  # excluding run_id + ingested_at
```

Prevents "we re-ran the backfill and now have duplicate rows" (ReplacingMergeTree
should absorb this, but idempotency tests catch cases where the business key
composition is wrong).

### 14.7 Code review — mandatory checklist per PR type

Beyond the PIT checklist in §13.4.4, every ingester/resolver/schema PR requires:

**Ingester PR:**
- [ ] Payload archive: does the ingester call `raw.archive` write before parsing?
- [ ] Resolver call: which `resolve_to_*()` function does this use?
- [ ] Unresolved handling: what happens when resolution returns `None`?
- [ ] Idempotency: re-running the ingester over the same payload produces same rows?
- [ ] Version bump: does `parser_version` bump if parse logic changed?
- [ ] Golden set: added at least 3 golden cases covering happy path + 2 edge cases?

**Resolver PR:**
- [ ] Confidence output: returns explicit `(target_id, confidence)` tuple?
- [ ] Golden regression: existing golden set still passes?
- [ ] New golden cases for any edge case the PR was written to solve?
- [ ] SCD-2 aware: for time-scoped resolution, `asof_date` is a parameter?

**Schema/migration PR:**
- [ ] Sort key rationale: why these columns in this order?
- [ ] Skip indices: filter-frequent columns not in sort key have skip indices?
- [ ] Partition strategy: retention story documented?
- [ ] `dataset_version` bump if this is a rewrite of existing data?
- [ ] Downstream MVs / research views updated?
- [ ] Test: schema conformance test asserts new columns/types?

**Research/backtest PR:**
- [ ] Uses `research.*` views only (PIT enforcement)
- [ ] `asof_date` threaded from config, not hardcoded
- [ ] `(dataset_version, code_version, input_hash)` logged in backtest run
- [ ] Universe membership uses SCD-2 filter (no survivorship bias)
- [ ] Corporate actions applied only if `ex_date <= asof_date`

### 14.8 What tests protect against (traceable benefits)

Each test category has a concrete failure mode it prevents:

| Test category | Bug class prevented | Real historical example |
|---|---|---|
| Amount parser golden set | Default-to-smallest-bucket (Sev-1) | Present in current `alt_political_trades` (99.6% defaulted) |
| Bioguide golden set | Name-normalizer regressions | Present now (26% orphan rate) |
| Ticker resolution golden set | Wrong ticker attribution | `BILL` for Treasury (present now) |
| Asset-type golden set | Type-inference mistags | `GE` tagged as option (present now) |
| SCD-2 no-overlap | Duplicate historical constituency | Would silently double-count returns |
| Idempotency | Duplicate rows from re-run | Would inflate volume/OI |
| PIT safety | Look-ahead bias | Backtest that reads restated fundamentals → overfitting |
| Restatement chain | Old view of fundamentals lost | Q3 2023 amended twice; research reads latest instead of asof |
| Coverage / freshness | Silent ingest failure | India ingester was down; dashboard didn't alert |
| Backtest reproducibility | Silent factor drift | Someone changes momentum formula, historical Sharpe silently shifts |

Every fault surfaced in the design review corresponds to a test category that
prevents its recurrence. **This is how the schema earns its rehau.**

---

## 15. Migration waves

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
1. Merge `us_*`, `india_*` operational tables → `meta.*`
2. Cutover dashboards / API
3. Drop old tables

**Wave 4 — Alt data generalization + political data quality (3-4 weeks)**

Driven by the 2026-09-19 political-data audit. Wave 4 is longer than v1 estimated
because the audit surfaced Sev-1 (amount defaulting) + Sev-2 (bioguide 74%,
asset-type mistags) + Sev-3 (SCD-2 bloat) issues that must all land before the
migration is meaningful.

Tier 1 — ingester bug fixes (do BEFORE any table rebuild):
1. **Fix amount parser** — read explicit bucket string; NULL if unreadable; NEVER default.
   Golden set `tests/golden/amount_parser/` seeded with 50+ cases across all seven brackets.
2. **Trust `filing_asset_type_code`** from PTR filing metadata; do not re-derive from
   ticker/name regex. Fixes `BILL`-for-Treasury, `GE`-tagged-as-option, `S`-for-mutual-fund class of bugs.
   Golden set `tests/golden/security_type/` seeded with 100+ audit-found mistags.
3. **Fix legislator name normalizer** — strip `Hon.` prefix, strip titles (`Dr`, `Jr`, `Sr`),
   collapse repeated first names, deduplicate whitespace; match against BOTH
   `legislators-current.yaml` and `legislators-historical.yaml`.
   Golden set `tests/golden/bioguide/` seeded with the current 26% orphan cases:
   `"Hon. Scott Scott Franklin"`, `"Hon. Richard Dean Dr McCormick"`, `"Hon. April McClain Delaney"`, etc.
   Target: 95%+ resolution rate.

Tier 2 — schema rebuild + resolver-driven backfill:
4. Rename `alt_political_*` → `alt.political_*` under new schema. Includes the
   expanded columns: `legislator_entity_id`, `security_type`, `contract_id`,
   `security_id`, `entity_id`, `resolution_confidence`, `bioguide_confidence`.
5. **Backfill canonical FKs via resolver:**
   - Ticker → `listing_id`, `security_id`, `entity_id` via `ref.identifier_aliases`
     (populated in Wave 1 from EODHD full US universe + LEI-GLEIF + OpenFIGI).
   - Options: parse OCC symbol → `contract_id` in `ref.contracts`.
   - Legislator name → `bioguide_id` → `legislator_entity_id` in `ref.entities`.
   - Unresolved rows land with `NULL` FKs + `resolution_confidence='unresolved'` →
     queued to `meta.unresolved_entities`. Never silently dropped.
   - Expected post-migration rates: ~90% ticker resolution, ~95% bioguide resolution,
     100% options resolution (OCC-parseable).
6. **Rebuild committee memberships as SCD-2.** Collapse 105K daily snapshots to
   ~30K period rows (`effective_from`, `effective_to`). Deduplicate the redundant
   daily state.
7. **Migrate legislators to `ref.entities` + `ref.legislator_terms`.**
   Existing `alt_political_legislators` (539 current) becomes ~1,500 entities
   (adding historical) with N term rows per entity in `ref.legislator_terms`.
   Retired members retain their `entity_id` — historical trade attribution works.

Tier 3 — coverage completion:
8. **Backfill Senate eFD from Postgres.** Memory says 6,514 rows exist in the
   Postgres `alt_political_us` schema. Migrate to ClickHouse `alt.political_trades`
   with `source_channel='senate_efd'`. Run the same resolver pipeline for canonical FKs.
9. **Backfill filing headers** for pre-2026 filings referenced by existing trades
   (currently trades reference filings back to Nov 2024 but filing headers only
   exist from 2026-01-01).
10. **Ingest historical committee memberships** for Congresses 117 and 118 (2021-2025).
    Needed for pre-2025 trade × committee-membership correlation queries.
11. **Create `legislator_trades_dedup` view** — cross-source dedup of House PTR ↔
    Senate eFD ↔ Senate Stock Watcher for the 2019-2020 overlap window. View was
    described in memory but doesn't exist in prod ClickHouse.
12. **Ingest annual FDs** (filing_type='FD'), not just PTRs. Captures income,
    honoraria, gifts, non-transaction holdings — enables broader wealth/exposure
    analytics.

Tier 4 — nice-to-have completeness (defer to Wave 4.5 if timeline pressures):
13. Add CUSIP extraction for bonds/munis (hard due to PDF variance).
14. Add House amendment tracking (`filing_type='A'`).
15. Add FEC contribution ingestion (already scoped in memory: FEC=A+B).
16. Add Congress.gov bills / lobbying (already scoped: Congresses 117-119, market-related policy areas).

**Wave 4 exit criteria:**
- Amount fidelity: 0% defaulted (all NULLs are true unreadable cases)
- Bioguide resolution: ≥95%
- Ticker → listing resolution: ≥85%
- Options → contract resolution: 100% for OCC-parseable
- All SCD-2 tables pass `test_scd2_no_overlap`
- All FK integrity checks green (<0.5% dangling per table)
- All 4 golden sets seeded and passing
- Senate coverage: parity with House

**Wave 5 — Fundamentals (3-4 weeks)**
1. `fundamentals.filings`, `fundamentals.line_items`
2. Backfill from EODHD Fundamentals ($60/mo) + SEC EDGAR XBRL
3. Build `snapshots_pit` MV for top 200 tags

**Wave 6 — Derived + backtests (2 weeks)**
1. `derived.factors`, `derived.signals`, `derived.computations`
2. Migrate existing factor calc code
3. Bitemporal enforcement in research views

**Wave 7 — Broker mirror + monitoring completeness (3 weeks)**

Tier 1 — dimensional foundation (do first, everything else FKs here):
1. Create `ref.broker_accounts` — seed from `ib.managedAccounts()` + one-time manual fill for `base_currency`, `booking_country`, `account_type`, `margin_type`
2. Create `ref.execution_methods` — seed `api_trade_engine_v1`, `api_research_notebook`, `manual_gui_or_mobile`, `algo_vwap`, `algo_adaptive`; extend as new clientIds register
3. Create `book.strategies` — populate with any active strategies (may be empty at Wave 7 start)
4. Create `meta.reconciliation_drift` and `meta.unresolved_entities` — schema-only until reconciler and resolver populate them
5. Populate `ref.identifier_aliases` with IBKR `conid` for the launch universe (S&P 500 members + all held positions across paper and live accounts)

Tier 2 — broker tables + core ingesters:
6. Create `broker.*` tables (`positions_snapshot`, `account_state_snapshot`, `executions`, `open_orders_snapshot`, `cash_flows`, `applied_corporate_actions`, `order_events`) + MVs (`positions_latest`, `nav_daily`, `margin_state_daily`)
7. Wire IBKR ingesters — dual Gateway (paper 4002, live 4001), per-`account_mode` dispatch:
   - Positions / account_state snapshotter (2× daily)
   - Executions puller (15-min intraday)
   - Open orders snapshotter (5-min intraday) — piggyback: for every `Trade` in `ib.trades()`, flush `Trade.log` entries into `broker.order_events` (idempotent by `perm_id, event_seq`)
   - Cash-flows ingester via IBKR Flex Query (daily T+1) — dividends, interest, deposits, FX conversions, tax withholding
   - FX-rate puller alongside positions snapshot (populates `fx_rate_to_base` PIT columns)
   - Per-position margin puller via `reqAccountSummary(RequestedTags='InitMarginReq,MaintMarginReq')` on model-portfolio scope
8. Backfill `broker.executions` from IBKR (30d rolling window at first; deep-backfill paper account if forward-test attribution needs further history)
9. Backfill `broker.cash_flows` from IBKR Flex historical (as far back as Flex serves — typically 365d)
10. Wire the execution-method resolver — every fill: `placed_by_client → ref.execution_methods.method_id`; unknown clientIds queue to `meta.unresolved_entities`

Tier 3 — reconciliation + attribution:
11. Build morning reconciliation job: compare AM position snapshot to prior EOD, generate `meta.reconciliation_drift` rows for unexpected deltas, suppress deltas that match `broker.applied_corporate_actions` for the same security+account+date
12. Wire `broker.applied_corporate_actions` populator: on every snapshot cycle, cross-reference position deltas against `ref.corporate_actions` for held tickers
13. Cutover portfolio dashboards to `broker.positions_latest` + `broker.nav_daily` + `broker.margin_state_daily`

Tier 4 — attribution scoring (dependent on trade engine writing `strategy_id`):
14. Build `derived.forward_test_attribution` populator (daily) — join `broker.nav_daily` (paper, per strategy_id) against `derived.backtest_returns` for the same strategy

Exit criteria:
- Both paper and live IBKR accounts land 2× daily snapshots for ≥5 consecutive sessions
- Executions ingest is idempotent (re-run of any window produces 0 duplicate rows post-merge)
- Cash flows ingest is idempotent + attributed to `security_id` where applicable
- `resolution_confidence='exact'` for ≥95% of positions in the launch universe
- FX PIT columns populated on every position row (nullable only for BASE-ccy positions)
- Reconciliation drift detection verified with a deliberate injected mismatch
- Applied corporate actions suppress the corresponding drift row (verified with a synthetic split)
- `broker.margin_state_daily` populated + one alert path (`cushion < 0.15`) wired
- `broker.order_events` captures ≥95% of state transitions for orders during the ≥5-consecutive-sessions verification window (measured against manual sampling of `Trade.log` for spot-check orders)
- No `placeOrder` / `cancelOrder` / `modifyOrder` calls anywhere in FactorLab source tree (CI grep guard green)

**Wave 8 — `book.*` and `risk.*` namespaces (themes, trade_names, budgets, attribution) (2 weeks)**

Depends on Wave 6 (`derived.*` foundations) and Wave 7 (`broker.*` populated).
Rev 7 splits what was one namespace into two — `book.*` for PM intent and
`risk.*` for governance — and renames `trade` → `trade_name` throughout.
Sequences dims-first so attribution has FK targets.

**`[blocker]` before Wave 9+ (added rev 11 per F2)**: if
`count(distinct broker_code) FROM ref.broker_accounts > 1` OR the FactorLab
book expands beyond a single-fund entity (a second pod, a client mandate, a
separately-managed account cohort), `ref.funds` MUST be built and every
`risk.budgets` row with `scope='fund'` MUST be migrated from
`scope_id='default_fund'` (the current string sentinel) to a real `fund_id`
UUID. The single-fund sentinel is defensible today because there is one book,
one fund entity, one attribution scope — but the sentinel does not compose
under a second fund and will silently mis-route budget lookups the day a
second fund's `scope='fund'` budget is inserted. This gate is the checkpoint
that catches the transition before it happens; see §17.1 Q6 for the design
rationale. Ownership: dba + risk sign-off jointly.

Tier 1 — dimensional foundation (`book.*` dims + `book.strategies` move):
1. Create `book.strategies` if not already created in Wave 7 tier 1 (rev 7 renamed target — was `derived.strategies`). If Wave 7 populated `derived.strategies`, migrate rows over and repoint FKs on `broker.executions`, `broker.open_orders_snapshot`, `broker.order_events`, `broker.cash_flows`, `derived.forward_test_attribution`.
2. Create `book.themes` — seed with the initial 3–5 active themes (one row per; parent_theme_id NULL for top-level)
3. Create `book.trade_names` — seed with any open trade_names (status='active'); backfill closed trade_names from historical broker.executions groupings if needed

Tier 2 — intent + bridge + `risk.*` foundation:
4. Create `book.trade_legs` — one row per (trade_name_id, listing_id) per active trade_name; PM authors targets via internal tool or YAML → migration
5. Create `book.attribution` — schema-only until executions start writing `trade_name_id` via `Order.orderRef`
6. Create `risk.budgets` — seed with the current fund-level budget (`scope='fund', scope_id='default_fund'`); add strategy/theme/trade_name budgets as risk authors them
7. Wire the attribution resolver: nightly job reads `broker.executions` where `trade_name_id IS NULL`, tries rule-based match against `book.trade_legs`, writes `book.attribution` rows with `confidence='high'` for exact matches

Tier 3 — broker extensions + rollups:
8. Alter `broker.positions_snapshot` — add `trade_name_id`, `theme_id`. Backfill from `book.attribution` + `book.trade_names` join for the trailing 90d
9. Alter `broker.executions`, `broker.open_orders_snapshot`, `broker.order_events` — add `trade_name_id`. Backfill from `book.attribution` for the same window
10. Build the four rollup MVs: `broker.exposure_by_theme_daily`, `broker.exposure_by_trade_name_daily`, `broker.pnl_by_theme_daily`, `broker.pnl_by_trade_name_daily`
11. Wire the risk-budget alerting: MV consumers check current exposure/PnL against active `risk.budgets` rows, fire alerts on breach

Tier 4 — PM tooling (not schema, but the tables aren't useful without it):
12. Internal CLI / notebook helpers to (a) open a trade_name, (b) amend trade_legs, (c) set/update risk budgets (risk-owned edit path, distinct from PM edits), (d) close a trade_name with reason. All go through the same resolver-only-writes pipeline as market/broker data.

Exit criteria:
- ≥1 theme with ≥1 active trade_name with ≥2 trade_legs, populated end-to-end
- `broker.positions_snapshot.trade_name_id` populated for ≥95% of positions in accounts running attributed strategies
- `risk.budgets` SCD-2 no-overlap test passes for all four scopes (`fund`, `strategy`, `theme`, `trade_name`)
- Budget-hierarchy inversion check (`trade_name sums ≤ theme cap ≤ strategy cap ≤ fund cap`) runs nightly, green
- `broker.exposure_by_theme_daily` reconciles with `sum(market_value)` from `broker.positions_snapshot` grouped by theme_id (within FX rounding)
- One trade_name-level stop-loss alert path wired and verified against a synthetic breach
- CI FK integrity check green for `theme_id`, `trade_name_id`, `strategy_id` denormalizations

**Total: ~17-19 weeks focused work** (Wave 8 adds 2 weeks to prior estimate).

---

## 16. What this doc explicitly does NOT cover

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

## 17. Deferred concerns with rationale

Items considered during review and deliberately deferred. Not gaps in
correctness — deferrals to earn scope.

| Item | Deferral | Rationale |
|---|---|---|
| **F2** — Data-quality-aware multi-vendor conflict resolution | Wave 2+ | Priority-based `bars_best` MV is fine for raw ingest. Cleaned/processed dataframes are downstream — quality-aware selection lives there, not in the raw curation layer. |
| **F3** — Sophisticated FX time-of-day handling (WM/Reuters 4pm etc.) | Post-launch | IBKR EOD snapshots for exposure currencies are sufficient for USD normalization at first. Multi-fix support added when specific research demands it. |
| **F11** — Continuous futures roll methodology (volume/OI/first-notice/proportional/gap) | With `derived.*` | Schema (`market.futures_continuous`) supports multiple methods; conventions defined when derived layer builds. |
| **F13** — Alt-data resolver SLA and review process | Post Wave 4 | Operational concern; put in place once alt data volume justifies it. |
| **F15** — Latency SLOs and pager/alert policy | Health module | Separate cross-cutting ops module owns this; schema-agnostic. |

### 17.1 Two-namespace split — `book.*` and `risk.*` (2026-09-20, rev 7 refactor)

Rev 6 placed themes / trade_names / risk budgets / attribution in `derived.*`.
Rev 7 splits them out. Rationale below.

**Q1 — Why not `derived.*`?**

`derived` is defined (see §1 and §9) as *reproducible computed data* —
factors, signals, portfolios, backtest returns. Every row in `derived.*` can
be re-derived from raw facts + a code_version + an input_hash. Themes, trade
names, risk budgets, and attribution assignments are none of that: they are
editorial acts by humans (the PM authors a theme, the risk desk sets a
budget, the ops analyst tags an unattributed fill). If we ever wiped and
recomputed `derived.*` we would want to preserve every row of `book.*` and
`risk.*` untouched. That semantic asymmetry is a namespace boundary, not a
convention.

**Q2 — Why two new namespaces instead of one (e.g., `intent.*` for everything)?**

Because at real multi-manager hedge funds (Millennium, Citadel, Balyasny,
Point72, Balyasny) *the PM owns the book and a separate risk function owns
the constraints*. "The book" is standard industry vocabulary for the
portfolio-level expression of a PM's investment views — strategies, themes,
named trades (positions expressing a thesis), and the composition of each.
Risk is a distinct governance function that sets gross/net caps, VaR budgets,
drawdown stops, and concentration limits, and enforces them independently of
the PM. Modeling these two ownerships as two ClickHouse databases means:
(a) grants can differ — risk can revoke PM write access to `risk.budgets`
without touching `book.*`; (b) audit trails don't co-mingle PM edits with
risk edits; (c) the `risk.*` namespace has room to grow (`risk.limits`,
`risk.exposure_snapshots`, `risk.var_daily`, `risk.drawdown_events`) without
disturbing `book.*`. Cheap now, structurally correct later.

**Q3 — What survives from rev 6, and what changed?**

All five tables from rev 6 survive as ClickHouse DDL. Namespace and one
column name changed:

| Rev 6 | Rev 7 |
|---|---|
| `derived.strategies` | `book.strategies` (moved from rev 5 home) |
| `derived.themes` | `book.themes` |
| `derived.trades` | `book.trade_names` (renamed table + `trade_id` → `trade_name_id`) |
| `derived.trade_legs` | `book.trade_legs` (column `trade_id` → `trade_name_id`) |
| `derived.position_attribution` | `book.attribution` (renamed + column change) |
| `derived.risk_budgets` | `risk.budgets` (`scope` enum: `'trade'` → `'trade_name'`) |

Broker extensions and rollup MVs updated to match: `trade_id` →
`trade_name_id`, `exposure_by_trade_daily` → `exposure_by_trade_name_daily`,
`pnl_by_trade_daily` → `pnl_by_trade_name_daily`. `derived.forward_test_attribution`
stayed in `derived` (it IS computed — paper NAV minus backtest return per
day) but its `strategy_id` FK now points at `book.strategies`.

The word `trade` alone is banned as a table or entity name going forward —
it collides with `broker.executions` (fills) and with `alt.political_trades`
(legislator PTRs, now renamed to `political_trade_id` for its PK). We say
`trade_name` for the fund-structure concept; `trade_leg` is fine because
"leg" disambiguates.

**Q4 — Future: `book.center`?**

Multi-manager funds run a concept called the "center book" — a fund-level
book that hedges or overlays pod-level positions. When a second pod appears
in FactorLab, `book.center` (a distinct `book.trade_names`-shaped table or a
scope flag on `book.trade_names`) fits naturally into this namespace. Rev 7
does not build it; naming it here reserves the concept.

**Q5 — ClickHouse verdict (carried from rev 6)?**

Unchanged: ClickHouse handles the workload well — shallow hierarchy (Fund → Strategy → Theme → Trade_name → Position leg, ≤5 levels, ≤ few hundred rows per level), aggregating-MVs fit the PnL rollup pattern, sub-second latency achievable for "current exposure by theme." Caveat also unchanged: live cross-scope risk-budget aggregation is not sub-second material — a small denormalized "current budgets" surface (roadmap: `risk.exposure_snapshots`) covers that gap when needed.

**Q6 — Structural gaps in rev 5 that rev 6/7 collectively address?**

Three, all addressed:

1. **No `trade_name_id` propagation.** `broker.executions` had `strategy_id` (rev 5) but nothing tying an execution to the specific *thesis* being expressed. Solved by `book.trade_names` + `book.attribution` bridge + `trade_name_id` denorm on the four broker fact tables.
2. **No risk-budget dimension.** Risk numbers were implicit in strategy config (`book.strategies.config_hash`) but not queryable as a versioned time series and not attachable to non-strategy scopes (fund, theme, trade_name). Solved by polymorphic `risk.budgets` keyed on `(scope, scope_id)` — and by putting it in the risk-owned namespace so the ownership boundary is legible.
3. **No thematic tag on positions.** Cross-strategy theme rollups ("what is our total AI infrastructure exposure across the mean-reversion strategy AND the discretionary book?") were impossible — themes didn't exist as a first-class object. Solved by `book.themes` + `theme_id` denorm on `broker.positions_snapshot` (hot read path).

A fourth gap remains: no formal `ref.funds` dim. Currently single-fund — using `scope_id='default_fund'` sentinel in `risk.budgets`. When a second fund appears, a `ref.funds` table gets added and the sentinel is retired to real UUIDs. Explicit deferral, not an oversight — and per rev 11 (F2), the deferral is now gated at Wave 9+ by an explicit `[blocker]` check on §15's Wave 8 entry: `count(distinct broker_code) FROM ref.broker_accounts > 1` OR any book expansion beyond a single-fund entity triggers `ref.funds` build + migration of every `scope='fund'` row off the sentinel. The gate is what makes the deferral survivable — without it, the sentinel silently mis-routes the day a second fund's budget row lands.

---

## 18. Revision changelog

### Revision 11 — 2026-09-20

Full findings resolution — every §19 finding F1 through F22 addressed;
systemic §3 DDL shorthand normalized to full-syntax `CREATE TABLE`; three
info findings (F23-F25) reviewed and noted as no-action. Load-bearing
changes below; per-finding disposition in the new §19.8.

Schema / DDL changes:
- **F1**: `ref.entities.entity_type LowCardinality(String)` declared —
  discriminator for the "same UUID space, different type" rule (`'issuer'`,
  `'person_legislator'`, `'person_pm'`, `'person_analyst'`,
  `'person_committee_member'`, `'committee'`, `'organization'`,
  `'government_body'`, `'other'`).
- **F4**: `alt.political_committees.committee_entity_id` and
  `alt.political_committee_memberships.committee_entity_id` /
  `legislator_entity_id` FK comments now name the required `entity_type`.
- **F6**: `ref.adjustment_factors` — added `version`, `ingested_at`,
  `as_of_time`; explicit `ENGINE = ReplacingMergeTree(version)` and
  `PARTITION BY toYear(event_date)`.
- **F8**: `broker.cash_flows.trade_name_id Nullable(UUID)` added — populated
  only for commission / fx_conversion rows.
- **F9**: `ref.securities.sector_gics_id` → `sector_id` +
  `sector_classification LowCardinality(String)` (values `'gics','icb',
  'naics','trbc'`).
- **F10**: `broker.account_state_snapshot.metric_canonical
  LowCardinality(String)` added; new dim `ref.broker_metrics_map` maps
  `(broker_code, vendor_metric) → canonical_metric`. `broker.nav_daily` and
  `broker.margin_state_daily` MVs rewritten to filter on `metric_canonical`.
- **F11**: `broker.applied_corporate_actions` gets
  `INDEX idx_security_id security_id TYPE bloom_filter(0.01) GRANULARITY 4`.
- **F16**: `alt.political_trades.amount_currency FixedString(3) DEFAULT 'USD'` added.
- **F19**: duplicate `alt.social_reddit_posts` DDL block deleted (was a
  bad-merge duplicate at line 1289 in rev 10; kept the canonical one).
- **F20**: `alt.political_filings.bioguide_confidence` enum spelled out
  (was `Enum8(...)`).
- **F22**: `book.trade_legs.side` → `book.trade_legs.leg_side` — clarity
  vs `book.trade_names.side` (different enum, same column name).

Prose / documentation changes:
- **F2**: Wave 8 in §15 gains an explicit `[blocker]` gate — the
  single-fund `'default_fund'` sentinel must be replaced with a real
  `ref.funds` dim before any second-fund / cross-book expansion. §17.1 Q6
  updated to reference the gate.
- **F3**: §2 namespace table reordered to build-wave dependency order
  (`ref`, `market`, `fundamentals`, `alt`, `book`, `risk`, `derived`,
  `broker`, `meta`, `raw`).
- **F5**: §13 renumbered — `§13.13 Canonical FK conventions` → `§13.11`.
  Rev-8 changelog line noting `§13.1-§13.13` corrected inline to
  `§13.1-§13.11` with a rev-11 fix note.
- **F7**: new **§13.12 Denormalization rule: IDs only, not attributes** —
  bridges denormalize canonical UUIDs, never mutable attributes.
- **F12**: `book.attribution` DDL followed by an explicit paragraph on
  open-order attribution (no bridge table; open orders trust `orderRef`).
- **F13**: new **§11.14 `research.owned_listings` view** — DDL sketch of
  the research-time helper for join semantics against alt-data facts.
- **F14**: `risk.budgets` prose now documents that position-level stops
  live in `book.trade_legs.stop_price`, not `risk.budgets`.
- **F15**: `market.options_bars` `PARTITION BY` line carries a
  `-- TODO measure at Wave 5+` comment (~720 partitions on the edge).
- **F17**: `ref.broker_accounts.strategy_ids` documented as CANONICAL;
  `book.strategies.paper_account_ids` / `live_account_ids` documented as
  denorm; §14 test category table gains a "Denorm bidirectional
  consistency" row.
- **F18**: each historical revision summary block at the top of the doc
  now carries an italic disclaimer that its numbering is as-written under
  its revision.
- **F21**: `market.bars` prose warns about `market.volume_by_product_type_daily`
  (reserved MV name, Wave 5+) and extends the never-cross-sum warning to
  `market.options_bars.volume`.

Systemic §3 sweep: every `ref.*` DDL block now uses full
`CREATE TABLE (...)` syntax, trailing commas, explicit `ENGINE = ...`,
explicit `ORDER BY`, and the standard audit pair (`version UInt64`,
`ingested_at DateTime64(3, 'UTC')`). Tables touched: `ref.countries`,
`ref.currencies`, `ref.exchanges`, `ref.sectors`, `ref.sources`,
`ref.entities`, `ref.entity_relationships`, `ref.securities`,
`ref.listings`, `ref.contracts`, `ref.identifier_aliases`,
`ref.corporate_actions`, `ref.adjustment_factors`, `ref.universes`,
`ref.universe_membership`. Already-full-syntax tables
(`ref.execution_methods`, `ref.broker_accounts`, `ref.dataset_versions`,
`ref.listing_migrations`, `ref.legislator_terms`, `ref.sessions`,
`ref.holidays`) left as-is. `market.*` and `broker.*` spot-checked;
already-consistent.

Info findings **F23**, **F24**, **F25** reviewed and left as-is — reasons in §19.8.

### Revision 10 — 2026-09-20

Full schema review of the rev-9 baseline. Findings landed in the new §19 (25
findings across severity tiers). Trivial mechanical fixes applied inline
(logged in §19.6); design-level findings left for user decision. No table
renames, no namespace moves, no engine or sort-key changes.

Inline fixes:
- `market.bars` — removed the `notional_usd` column DDL line (the prose two
  paragraphs below already declared it MV-computed, not stored — one had to
  give). MV name to build in Wave 2: `market.bars_notional_usd`.
- `§14.7` code-review checklist — stale `10.4.4` reference bumped to `§13.4.4`
  (rev-8 renumber orphan).
- `ref.sessions` — added missing column-separator commas, `version,
  ingested_at`, and `ENGINE = ReplacingMergeTree(version)` clause (was
  three-way inconsistent with the sibling `ref.holidays` DDL).
- `ref.holidays` — added missing `ENGINE = ReplacingMergeTree(version)` clause
  (was implicit).

Everything else — including missing `entity_type` on `ref.entities`, the
§13.11 / §13.12 numbering gap, missing PIT columns on `ref.adjustment_factors`,
and 20+ other findings — documented in §19 without modification.

### Revision 9 — 2026-09-20

`ops` renamed to `meta` — the namespace is data observability, not trading
ops. Aligned to industry data-observability vocabulary (freshness / volume /
schema / distribution / lineage). No schema changes, only rename.

- **§10 heading**: `## 10. \`ops\` — Operational` → `## 10. \`meta\` — Data observability`, with a two-paragraph framing explaining the rename rationale (observability layer for the data platform vs trading ops in the trade engine) and calling out the naming rhythm match (`ref`, `alt`, `raw`, `book`, `risk`, `meta`).
- **§10 tables renamed** (schema qualifier only — same columns, same engines, same sort keys, no DDL changes beyond the `ops.` → `meta.` prefix):
  - `ops.ingestion_runs` → `meta.ingestion_runs`
  - `ops.expected_series` → `meta.expected_series`
  - `ops.session_coverage` → `meta.session_coverage`
  - `ops.recovery_state` → `meta.recovery_state`
  - `ops.source_status` → `meta.source_status`
  - `ops.lineage` → `meta.lineage`
  - `ops.reconciliation_drift` → `meta.reconciliation_drift`
  - `ops.unresolved_entities` → `meta.unresolved_entities`
- **§2 namespace layout table**: `ops` row renamed to `meta`; Purpose column rewritten to "Data observability: pipeline health (ingestion runs, coverage, source status, expected series), lineage, reconciliation drift, unresolved-entity queue — the data about our data." Retention unchanged (2y hot, 5y cold).
- **Cross-references updated across the doc:**
  - §5 (`fundamentals.line_items` FK comment on `ingest_run_id`)
  - §6 (`market.bars` lineage FK comment)
  - §7.7 FK-integrity summary (`meta.integrity_checks`)
  - §8 `risk.budgets` rollup check (`meta.integrity_checks`)
  - §11.1 `broker.positions_snapshot` nullable-canonical-IDs prose (`meta.unresolved_entities`)
  - §11.7 broker reconciliation contract — including the load-bearing trade-engine boot check `SELECT count(*) FROM meta.reconciliation_drift WHERE …`
  - §11.10 `broker.applied_corporate_actions` reconciler comment (`meta.reconciliation_drift`)
  - §13.2 time-zone storage bullet (`market.*`, `fundamentals.*`, `alt.*`, `meta.*` UTC)
  - §13.9 entity-resolution failure policy (`meta.unresolved_entities` as review queue)
  - §13.13 canonical FK enforcement — nightly integrity job renamed to `meta.integrity_checks`
  - §15 Wave 3 (operational-table merge target now `meta.*`), Wave 4 tier 2 (unresolved-entity queue), Wave 7 tier 1 + tier 3 (`meta.reconciliation_drift`, `meta.unresolved_entities`)
- **Top-of-doc Status line** bumped to `revision 9`.
- **Historical changelog entries preserved**: rev 8 renumbering table (`§8 | §10 | \`ops\``), rev 4 broker-companion-table note (`ops.reconciliation_drift`), and rev 2 F9 (`ops.lineage` retention) retain their as-written `ops.*` names — the changelog is history, not a link target.
- **Not touched**: no column renamed, no engine changed, no sort key reordered, no PARTITION BY changed, no TTL clause altered. Migration cost = `RENAME DATABASE ops TO meta` + downstream code find/replace on the schema qualifier. Every FK still points to the same UUID target; every dashboard query needs only the schema-name update.

### Revision 8 — 2026-09-20

Pure numbering + audit refactor. No table renames, no namespace moves, no
migration required for existing rev-7-authored code — additive columns only.

- **Section renumber for logical flow.** Rev 7 hung `book` and `risk` off `§7`
  as suffixes (`§7A`, `§7B`) to avoid disturbing existing numbers. Rev 8
  promotes them to first-class sections and physically reorders so the doc
  reads intent → compute → ops → broker. New numbering:

  | Rev 7 | Rev 8 | Purpose |
  |---|---|---|
  | §7 | §9 | `derived` |
  | §7A | §7 | `book` |
  | §7B | §8 | `risk` |
  | §8 | §10 | `ops` |
  | §9 (all §9.x subsections) | §11 (all §11.x) | `broker` |
  | §10 | §12 | `raw` |
  | §11 (all §11.x subsections) | §13 (all §13.x) | Cross-cutting |
  | §11.4.1–.5 (mis-numbered `10.4.x` under rev 7) | §13.4.1–.5 | Grant/research/CI/PR-template |
  | §12 (all §12.x) | §14 (all §14.x) | Testing |
  | §13 | §15 | Migration waves |
  | §14 | §16 | NOT covered |
  | §15 (`§15.1` inside) | §17 (`§17.1`) | Deferred concerns |
  | §16 | §18 | Changelog |

  Every live in-body cross-reference (`§9.7`, `§9.10`, `§11.9`, `§7A`, `§7B`,
  `§7` when it meant derived) updated. Rev-7 changelog block and older
  revision summaries (rev 4, rev 3, rev 2) preserved with their
  as-written numbers so history stays true to what was written when — the
  changelog is history, not a link target.

- **Fund-structure connectivity audit — new §7.7.** Full column and FK walk
  through `book.strategies`, `book.themes`, `book.trade_names`,
  `book.trade_legs`, `book.attribution`, `risk.budgets`,
  `broker.positions_snapshot`, `broker.executions`,
  `broker.open_orders_snapshot`, `broker.order_events`, and
  `derived.forward_test_attribution`. Top-down (`theme → trade_name → legs
  → attribution → executions → positions`) and bottom-up (`execution →
  attribution → trade_name → theme → strategy → risk.budgets`) join paths
  written out as copy-pasteable SQL. Verified: no FK type mismatch, no
  broken sort keys, PIT columns present on every fact table.

- **Additive columns from the audit** — all `Nullable`, no backfill risk:

  | Table | Column | Reason |
  |---|---|---|
  | `book.trade_names` | `base_currency FixedString(3)` | Trade-name-level PnL in the currency the PM authored the thesis in — currently every trade_name inherits fund base ccy which fails for a JPY-only pair trade priced in the PM's mental model in JPY. |
  | `book.trade_names` | `priority Enum8('high','medium','low','watch')` | Conviction rating — PM dashboard groups active trade_names by conviction; alerting thresholds tighten on `high`. |
  | `book.trade_names` | `owner_entity_id Nullable(UUID)` | Who currently runs the trade (may differ from `opened_by_entity_id` after PM handoff). |
  | `book.themes` | `owner_entity_id Nullable(UUID)` | Theme sponsor / responsible PM or analyst; enables "themes I own" filter. |
  | `book.trade_legs` | `entered_at_price_avg Nullable(Decimal(20,6))` | Realized entry basis at leg level (fill-weighted average) — MTM PnL at trade_name grain without re-aggregating `broker.executions` on every read. Populated by attribution resolver. |
  | `book.attribution` | `computation_id Nullable(UUID)` | Lineage for rule-based assignments — which resolver run produced this row; joins to `derived.computations`. Manual assignments leave it NULL. |

- **No columns removed. No FK repointed. No sort key reordered.** The rev-8
  audit found the existing sort keys already lead with the dominant query
  filter (`book.attribution` leads with `trade_name_id`, `risk.budgets`
  leads with `(scope, scope_id, effective_from)` — both correct).

### Revision 7 — 2026-09-20

Two-namespace split of the rev-6 fund-structure additions. **Supersedes rev 6
same-day.** Rev 6 placed strategies / themes / trade names / risk budgets /
attribution in `derived.*`. That is the wrong home: `derived.*` is
*reproducible computed data*, and none of those tables are computed —
they are human-authored PM intent and human-authored risk governance. Rev 7
splits them into two new namespaces that mirror how real multi-manager hedge
funds organize (Millennium / Citadel / Balyasny vocabulary).

- **New `book.*` namespace — PM-authored investment intent** (§7A). Contains
  `book.strategies` (moved from `derived.strategies`), `book.themes` (from
  `derived.themes`), `book.trade_names` (renamed from `derived.trades`),
  `book.trade_legs` (from `derived.trade_legs`), `book.attribution` (renamed
  from `derived.position_attribution`). The "book" is standard hedge-fund
  vocabulary — the PM owns it.
- **New `risk.*` namespace — Risk-owned governance** (§7B). Contains
  `risk.budgets` (moved from `derived.risk_budgets`, `scope` enum value
  `'trade'` → `'trade_name'`). Roadmap for the namespace (called out but not
  built rev 7): `risk.limits`, `risk.exposure_snapshots`, `risk.var_daily`,
  `risk.drawdown_events`.
- **Naming**: the word `trade` alone is banned as a table/entity name — it
  collides with `broker.executions` (fills) and with `alt.political_trades`
  (legislator PTRs). The fund-structure concept is `trade_name`. Rev 7
  renames every occurrence: `trade_id` → `trade_name_id` on `book.*`,
  `broker.*` fact tables, and rollup MVs. `alt.political_trades.trade_id`
  renamed to `political_trade_id` to keep the grep clean.
- **`derived.forward_test_attribution` stays put** — it *is* computed
  (paper NAV minus backtest return, per day). Only its `strategy_id` FK
  changes target, now pointing to `book.strategies`.
- **Broker extensions** (§9.12): `trade_id` → `trade_name_id` on
  `positions_snapshot`, `executions`, `open_orders_snapshot`, `order_events`.
  `theme_id` denorm on `positions_snapshot` unchanged.
- **Rollup MVs** (§9.13): `broker.exposure_by_trade_daily` →
  `broker.exposure_by_trade_name_daily`; `broker.pnl_by_trade_daily` →
  `broker.pnl_by_trade_name_daily`. `by_theme_daily` names unchanged.
- **Canonical FKs** (§11.13): `strategy_id`, `theme_id`, `trade_name_id` all
  originate under `book.*`. Resolver-only-writes rule extended;
  `book.attribution` is the single write path for `trade_name_id` on
  `broker.*` facts.
- **Namespace table** (§2): two new rows (`book`, `risk`); `derived`
  description tightened to "reproducible computed data" so the boundary is
  legible.
- **Testing** (§12.1): "Fund-structure PIT + FK" renamed "Book + risk PIT +
  FK"; assertions repointed at new namespaces.
- **Wave 8 rewritten** — same two-week duration, dims-first sequencing, but
  now `book.strategies` (move from Wave 7 `derived.strategies`) → `book.themes`
  → `book.trade_names` → `book.trade_legs` → `book.attribution` → `risk.budgets`
  → broker column adds → rollup MVs. Total migration estimate stays 17–19
  weeks.
- **§15.1 rewritten** — records the two-namespace rationale (industry
  vocabulary, ownership boundary, grant separability), lists what moved
  where, and notes that `book.center` (multi-manager center-book concept)
  fits naturally when a second pod appears.

### Revision 6 — 2026-09-20 — SUPERSEDED BY REV 7 SAME-DAY

Fund-structure tables introduced under `derived.*` (`derived.themes`,
`derived.trades`, `derived.risk_budgets`, `derived.trade_legs`,
`derived.position_attribution`) with `trade_id` denorm on the four broker
fact tables and four rollup MVs (`broker.exposure_by_theme_daily`,
`broker.exposure_by_trade_daily`, `broker.pnl_by_theme_daily`,
`broker.pnl_by_trade_daily`). Namespace placement was rejected same day
because strategies/themes/trade_names/risk_budgets are PM-authored intent
and risk-authored governance respectively, not reproducible computation.
See rev 7 above — the tables survive, only their namespace and one column
name (`trade_id` → `trade_name_id`) changed.

### Revision 4 — 2026-09-19

Broker/portfolio monitoring added as first-class namespace.

- **New `broker` database** (section 9) — mirrors broker-side truth as time
  series: positions, account state (NetLiquidation/BuyingPower/margin/etc.),
  executions (immutable event log by `exec_id`), open orders. Every row tagged
  with `broker_code`, `account_id`, `account_mode` — paper and live coexist,
  never mixed silently.
- **Read-only contract** documented explicitly: FactorLab code never calls
  `placeOrder`/`cancelOrder`. Execution runs in a separate trade-engine service
  that reads `broker.executions` for attribution.
- **Materialized helpers** — `broker.positions_latest` (current portfolio per
  broker+account), `broker.nav_daily` (equity-curve source for forward-test
  attribution).
- **Wave 7 added** to migration plan — 2 weeks, exit criteria include 95%
  canonical resolution on launch universe and idempotent execution ingest.
- **`ops.reconciliation_drift`** introduced as a small companion table for the
  morning position drift check (Wave 7 delivers it).
- **Namespace table** updated with the new `broker` row.
- **Renumber**: previous sections 9–15 shifted to 10–16 to fit the new
  section 9. Cross-refs in rev 3 changelog updated to current numbering.

### Revision 3 — 2026-09-19

Political-data audit findings + test discipline + FK conventions folded in.

- **`ref.legislator_terms` added** (SCD-2). Legislators become entities with
  term-scoped attributes lifted out; retired members retain `entity_id`,
  historical trade attribution never breaks.
- **`alt.*` section rewritten** with fully-defined political tables reflecting
  audit findings: canonical FK columns (`legislator_entity_id`, `security_id`,
  `listing_id`, `contract_id`, `entity_id`), resolution confidence enums,
  raw-vs-resolved separation, SCD-2 committee memberships (was daily snapshots).
- **Section 11.13 — Canonical FK conventions.** Explicit statement of the four
  canonical FactorLab UUIDs + person canonical; four defenses for FK integrity
  (resolver-only writes, nightly integrity job, CI conformance, PR checklist).
- **Section 12 — Testing and code review discipline** (NEW). Eleven test
  categories with coverage targets; golden-set-driven regression on all
  resolvers/parsers; PIT-safety tests; SCD-2 no-overlap; idempotency;
  bug-class-to-test-category traceability table.
- **Wave 4 fully expanded** to 4 tiers with concrete ingester fixes (amount
  parser, asset-type trust, name normalizer), resolver-driven schema rebuild,
  coverage completion (Senate backfill from Postgres, historical committees,
  annual FDs, dedup view), and explicit exit criteria.

### Revision 2 — 2026-09-19

Post-review changes based on prop-shop-lens vetting.

**F1 — Enforced PIT** (section 11.4)
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

---

## 19. Full schema review — 2026-09-20 (rev 9 baseline)

### 19.1 Executive summary

The rev-9 doc is in good shape after 9 revisions. The 2026-09-20 marathon
(rev 5 broker/portfolio → rev 6 fund-structure → rev 7 book+risk split →
rev 8 renumber → rev 9 ops→meta rename) has left a few loose threads but
the overall structure holds: 10 namespaces (`ref`, `market`, `fundamentals`,
`alt`, `book`, `risk`, `derived`, `meta`, `broker`, `raw`) are defensible,
FK conventions are documented, PIT correctness is enforced by four defenses,
and the fund-structure walk in §7.7 already caught six additive columns and
verified sort keys. Query-walk verdict: all five archetypal queries from
the audit checklist resolve in ≤ 2 joins as principle §1 promises.

**Findings count**: 25 total.

| Severity | Count |
|---|---|
| Critical (fix before build) | 2 |
| High | 6 |
| Medium | 9 |
| Low | 5 |
| Info | 3 |
| Trivial fixes applied inline | 4 |

**Categories where the doc is unexpectedly clean**: PIT / bitemporal coverage
(category G — every fact row has `event_time` + `as_of_time` + `version` +
`ingested_at`; four defenses cataloged; test category exists); FK
consistency for the `broker` × `book` × `risk` graph (category C — §7.7
audit already walked every FK type match, zero mismatches found); the
canonical UUID census (category C — §13.13 already enumerates the seven
canonical IDs with their homes and denorm targets).

**Categories with a systemic gap**: (a) small-dim DDL shorthand is
inconsistent — some `ref.*` tables in §3 use full `CREATE TABLE` +
commas + ENGINE while others use column-list-only shorthand without
commas (creating actual SQL-invalid blocks); the convention needs to
be picked one way. (b) `ref.entities.entity_type` is referenced in
prose three times as the discriminator between `person_legislator`,
committee-entity, and issuer entities but is not declared in the DDL —
a load-bearing column is missing.

### 19.2 Critical findings (fix before build)

**F1** [critical] [B/H] `ref.entities`: `entity_type` column referenced in
prose 3× (§3 `ref.legislator_terms` intro at line 586, §13.13 canonical
FK table at line 2848, and implicitly by `alt.political_committees`
using `committee_entity_id → ref.entities`) but NOT declared in
`ref.entities` DDL (§3, lines 301-311). Without `entity_type`, resolvers
cannot distinguish `person_legislator` from `issuer` from `committee`
from `person_analyst` (`book.themes.author_entity_id`) from `person_pm`
(`book.trade_names.owner_entity_id`). The whole "same UUID space,
different type" argument in §13.13 depends on this column existing.
Recommendation: add `entity_type LowCardinality(String) -- 'issuer',
'person_legislator', 'person_pm', 'person_analyst', 'committee', ...`
to `ref.entities` and enum-restrict values via a check at ingest.

**F2** [critical] [K] `ref.funds` dim: `risk.budgets.scope='fund'` uses a
hardcoded string sentinel `scope_id='default_fund'`. §17.1 Q6 and §15.1
both call this out as an "explicit deferral, not an oversight." That's
defensible today but the deferral MUST be lifted before a second fund /
pod / account cohort exists, and there is no gate anywhere in the migration
waves that flags it. Recommendation: add an explicit **Wave 8+ prerequisite
check** — "if `count(distinct account_id) FROM ref.broker_accounts WHERE
purpose LIKE 'client_mandate_%' > 0` then `ref.funds` must exist before
adding more `risk.budgets` rows." Or promote the deferral to a `[blocker]`
tag in §17.

### 19.3 High-severity findings

**F3** [high] [A/H] Namespace count mismatch: prose in §1 and rev-9
changelog says "10 namespaces" (`ref`, `market`, `fundamentals`, `alt`,
`book`, `risk`, `derived`, `meta`, `broker`, `raw`) but §2 namespace-layout
table lists only these 10 in a fixed order that does NOT match the actual
build-order dependencies (`book` depends on `ref.broker_accounts`; `risk`
depends on `book.strategies`/`themes`/`trade_names`). Not wrong, but
readers will look for `book` next to `broker` and find it eight rows away.
Recommendation: reorder §2 table to group namespaces by build wave (dims
first: `ref` → then facts: `market`, `fundamentals`, `alt` → then intent:
`book`, `risk` → then compute: `derived` → then observability + broker:
`meta`, `broker` → then archive: `raw`).

**F4** [high] [C/H] `alt.political_committees.committee_entity_id →
ref.entities` (line 1229) requires `entity_type='committee'` on
`ref.entities`. Blocks on F1.

**F5** [high] [H] §13 numbering gap: §13 has subsections 13.1 through
13.10 and then jumps directly to 13.13. §13.11 and §13.12 are missing.
Rev 8 note (line 34) says "§11.1-§11.13 (cross-cutting) → §13.1-§13.13"
implying 13 subsections. Either two subsections were dropped during
renumbering without adjusting the tail, or the count is wrong. Recommendation:
renumber `§13.13 Canonical FK conventions` → `§13.11`, and update the two
in-body references (line 2854/2856 mention "§13.13" implicitly via context;
verify all consumers). OR add back §13.11 (multi-vendor conflict scope
extension) and §13.12 (something) if they were dropped by mistake.

**F6** [high] [B] `ref.adjustment_factors` (§3, lines 517-524) has neither
`version` nor `ingested_at` nor `as_of_time`, and no `ENGINE` clause is
declared. §13.3 says "nightly job recomputes `ref.adjustment_factors`" —
that recompute must be versioned. Today's design allows an in-place
overwrite that would silently rewrite historical `bars_adjusted` results.
Recommendation: add `version UInt64` + `ingested_at DateTime64(3, 'UTC')`
+ `as_of_time DateTime64(3, 'UTC')`, engine `ReplacingMergeTree(version)`,
and pin `market.bars_adjusted` reads to a `dataset_version` from
`ref.dataset_versions`.

**F7** [high] [D] Denormalization inconsistency on `book.attribution`: the
table denorms `theme_id` and `strategy_id` (from `book.trade_names`) but
does NOT denorm the `book.trade_names.priority`, `book.trade_names.base_currency`,
or `book.themes.owner_entity_id` — three columns that PM dashboards will
filter on. Every dashboard query on `book.attribution` will need to join
`book.trade_names` and `book.themes` to get them. That's tolerable, but
document the choice — either say "we denorm only IDs, not attributes"
as an explicit rule (and audit `book.attribution` today for compliance)
or accept the join cost and note it.

**F8** [high] [I] `broker.cash_flows` NOT extended with `trade_name_id`
(§11.12): defensible for dividends (theme/strategy grain right), but
`cash_flow_type='commission'` rows WOULD benefit — commission per trade_name
is exactly the grain you want for post-trade cost analysis. Consider:
either add `trade_name_id Nullable(UUID)` to `broker.cash_flows`
(populated only for `cash_flow_type IN ('commission', 'fx_conversion')`
via the `linked_exec_id → book.attribution` join), or explicitly document
that commission-per-trade_name analysis goes through
`broker.executions.commission` instead and `cash_flows` is dividend/interest/
deposit-only.

### 19.4 Medium-severity findings

**F9** [medium] [B] `ref.securities` `sector_gics_id` is a single column but
§3 `ref.sectors` allows multiple classification systems (`'gics','icb','naics'`).
A security in a non-US market may only have ICB, not GICS. Recommendation:
either rename to `sector_id` + `sector_classification` (pair), or add
`sector_icb_id`, `sector_naics_id` companion columns.

**F10** [medium] [A/K] `broker.account_state_snapshot` metric column carries
IBKR-native tag names verbatim (`'NetLiquidation'`, `'BuyingPower'`, ...).
A second broker (Schwab) will use different names for the same concept.
Recommendation: add `metric_canonical LowCardinality(String)` populated from
a `ref.broker_metrics_map(broker_code, vendor_metric, canonical_metric)`
dim so cross-broker `nav_daily` and `margin_state_daily` MVs work uniformly.
Currently `broker.nav_daily` MV hardcodes `metric = 'NetLiquidation'` —
breaks on Schwab.

**F11** [medium] [E] `broker.applied_corporate_actions` `ORDER BY (broker_code,
account_id, event_time, security_id, corp_action_id)` — but the join back to
`meta.reconciliation_drift` (line 1889 says "reconciler consults that table
first") is `WHERE broker_code=? AND account_id=? AND security_id=? AND
event_time BETWEEN prev_snapshot_time AND curr_snapshot_time`. `security_id`
is deep in the sort key — that lookup will scan. Recommendation: add a
bloom-filter skip index on `security_id`.

**F12** [medium] [I] `book.attribution` bridges executions to trade_names
but there is no attribution table for OPEN ORDERS. The trade engine places
an order with `orderRef='trade_name:<uuid>'`, and `broker.open_orders_snapshot`
picks up the `trade_name_id` denorm — but if the resolver logic changes,
there's no bridge table to replay it against open orders. Positions and
executions both get their `trade_name_id` from `book.attribution`;
open_orders_snapshot gets it directly from `orderRef` parse. Two paths,
one concept. Recommendation: either (a) extend `book.attribution` to also
key on `perm_id` (durable order ID) so it covers open orders too, or
(b) explicitly document that open orders trust `orderRef` and no
attribution history is kept.

**F13** [medium] [J] Query walk #4 — "Which senator's PTRs from 2025 hit
tickers I now own?" — requires joining `alt.political_trades` to
`broker.positions_snapshot` on `listing_id`. Both tables have
`listing_id`. That works. But `positions_snapshot.listing_id` is
`Nullable(UUID)` (per resolver-may-miss policy) so the join loses
positions with `listing_id IS NULL`. Not a correctness issue but a
completeness gap that will show up as "18% of my positions have no
matching PTR" without the reader knowing whether that's PTR coverage
or resolution failure. Recommendation: publish a `research.owned_listings`
view that filters `positions_snapshot` to `WHERE listing_id IS NOT NULL`
so the join has documented semantics.

**F14** [medium] [I] `risk.budgets.scope` enum lists
`'fund','strategy','theme','trade_name'` — no `'position'` scope. A
single-name stop-loss at the position level (e.g. "if NVDA drops below
$180 close the leg") does not fit. Today expressed by
`book.trade_legs.stop_price`; but that's a target, not a governance
constraint. Recommendation: either explicitly document that position-
level stops live in `book.trade_legs` (PM-owned) not `risk.budgets`
(risk-owned) — a real governance boundary — or add `'position'` scope.
Prefer the former (documented) since ownership boundary is deliberate.

**F15** [medium] [E] `market.options_bars` `PARTITION BY (toYYYYMM(expiry),
toYYYYMM(bar_time))` produces `# expiry months × # bar months` partitions —
for 5y of monthly options across 12 expiries active at once that's
5×12×12 = 720 partitions, plus weeklies. That is borderline OK but on
the edge. Recommendation: measure at Wave 5+ with real data; consider
`PARTITION BY (toYear(expiry), toYYYYMM(bar_time))` if part count grows
too much.

**F16** [medium] [F] `alt.political_trades.amount_min` / `amount_max`
declared `Nullable(UInt64)` but the buckets are ranges (e.g. `$1K-$15K`
= 1000 to 15000). Fine for USD but if EU/UK filings ingest with
different currency, the UInt64 loses the currency context. Recommendation:
add `amount_currency FixedString(3) DEFAULT 'USD'` — cheap now, prevents
a schema surgery later.

**F17** [medium] [C/D] `derived.forward_test_attribution.account_id` is
`String` — matches `ref.broker_accounts.account_id String`. Good. But
`ref.broker_accounts.strategy_ids Array(UUID)` and
`book.strategies.paper_account_ids Array(String)` are a redundant pair
(§7.7 already flagged this). Which is the source of truth for
"which strategies run on which account"? Recommendation: document one
side as canonical and the other as a denormalization; add nightly
integrity check that the two agree.

### 19.5 Low-severity + info findings

**F18** [low] [H] Rev-7 summary block at top of doc (line 66-87) references
`§11.13, §12.1, §15.1, §16` — those are rev-7 numbers. Rev-8 note (line 36)
declares these are "as-written historical" but a first-time reader hitting
the top of the doc will hit stale refs before reading the disclaimer.
Recommendation: prefix each historical revision summary block with a
one-line "(numbering below is as-written under revision N; see §18 for
current numbering)".

**F19** [low] [B] `alt.social_reddit_posts` DDL appears TWICE — once at
line 1256 and again at line 1289 (identical shape, one has slightly
different comments on `mentioned_listings`). Likely a bad merge from a
prior revision. Recommendation: delete the second occurrence.

**F20** [low] [B] `alt.political_filings.bioguide_confidence Enum8(...)`
uses `...` shorthand (line 1176) instead of the six-value enum spelled out
in `alt.political_trades.bioguide_confidence`. Not wrong, but a reader
grep for the enum values won't find this occurrence. Recommendation:
spell it out.

**F21** [low] [F] `market.bars.volume Nullable(UInt64)` for shares OR
contracts. `market.options_bars.volume Nullable(UInt64)` — same. Prose
warns "never sum across `product_type`." Add a check constraint that
this warning is enforced — a MV `market.volume_by_product_type_daily`
that partitions strictly would guard against future users cross-summing.

**F22** [low] [I] `book.trade_legs.side` is `LowCardinality(String) -- 'long' | 'short'`.
But `book.trade_names.side` is `LowCardinality(String) -- 'long_only',
'short_only','pair','basket','long_short','event_driven'`. Two different
enums using the same column name. Not a bug (leg-side ≠ trade-side) but
easy to confuse. Recommendation: rename `book.trade_legs.side` →
`book.trade_legs.leg_side` for clarity.

**F23** [info] [K] No `raw.stream_batch` table — `raw.archive` handles
both HTTP payloads AND streaming batches via `window_start_at` /
`event_count` columns. That's fine at current volume but if IBKR
streaming or Reddit firehose is added, one table for batch-shaped and
stream-shaped payloads may be limiting. Not a today-fix. Documented.

**F24** [info] [L] `market.trades` and `market.orderbook_l2` are reserved
names (line 899) — no DDL. Consistent with §16 "deferred." OK.

**F25** [info] [K] `research.owned_listings`, `research.listing_continuous_returns`
mentioned but no DDL. Both are read-time views over `broker.*` and
`ref.listing_migrations` respectively. Fine to define in the migration
wave that builds them (Wave 2 for continuous-returns, Wave 7 for owned).

### 19.6 Trivial + rev-11 fixes applied in this pass (log)

Rev 10 landed T1-T4. Rev 11 continues the log with T5-T20; every finding
that required a DDL / prose edit has an entry.

| # | File location | Fix |
|---|---|---|
| T1 | `market.bars` DDL (§4, line ~730) | (rev 10) Removed the `notional_usd Nullable(Decimal(24,4))` column line — prose two paragraphs below already declared it MV-computed, not stored. Replaced with a `-- notional_usd is NOT stored here — see market.bars_notional_usd MV, §13.8` comment. Also fixes the missing trailing comma bug the ghost column carried. |
| T2 | `§14.7` code-review checklist | (rev 10) Bumped stale `10.4.4` reference to `§13.4.4` (rev-8 renumber orphan). |
| T3 | `ref.sessions` DDL (§3) | (rev 10) Added missing column-separator commas on 7 columns; added `version, ingested_at` audit pair; added `ENGINE = ReplacingMergeTree(version)` clause. Was three-way inconsistent with sibling `ref.holidays`. |
| T4 | `ref.holidays` DDL (§3) | (rev 10) Added missing `ENGINE = ReplacingMergeTree(version)` clause (implicit before). |
| T5 | `ref.entities` DDL (§3) | (rev 11 / F1) Added `entity_type LowCardinality(String)` with the nine-value enum; added prose paragraph explaining the discriminator's role. |
| T6 | §15 Wave 8 | (rev 11 / F2) Added an explicit `[blocker]` gate promoting the single-fund `default_fund` sentinel deferral to a wave-check on `count(distinct broker_code) FROM ref.broker_accounts > 1` OR any book expansion. §17.1 Q6 updated to cross-reference. |
| T7 | §2 namespace layout | (rev 11 / F3) Reordered rows to build-wave dependency order (`ref` → `market` → `fundamentals` → `alt` → `book` → `risk` → `derived` → `broker` → `meta` → `raw`). Added a one-line explanatory preamble above the table. |
| T8 | `alt.political_committees` + `alt.political_committee_memberships` DDL | (rev 11 / F4) FK comments now name required `entity_type='committee'` / `'person_legislator'`. |
| T9 | §13 renumber | (rev 11 / F5) `§13.13` → `§13.11`. Rev-8 changelog note (line 70) corrected in place with a rev-11 fix marker. |
| T10 | `ref.adjustment_factors` DDL (§3) | (rev 11 / F6) Added `version UInt64`, `ingested_at DateTime64(3, 'UTC')`, `as_of_time DateTime64(3, 'UTC')`; explicit `ENGINE = ReplacingMergeTree(version)`, `PARTITION BY toYear(event_date)`. Added prose about nightly recompute + `dataset_version` pinning. |
| T11 | New §13.12 | (rev 11 / F7) New subsection "Denormalization rule: IDs only, not attributes" — codifies the policy that `book.attribution` already follows. |
| T12 | `broker.cash_flows` DDL (§11.9) | (rev 11 / F8) Added `trade_name_id Nullable(UUID)` for commission/fx_conversion attribution. §11.12 extensions table updated; "not extended" list rewritten. |
| T13 | `ref.securities` DDL (§3) | (rev 11 / F9) Renamed `sector_gics_id` → `sector_id`; added companion `sector_classification LowCardinality(String)`; `ref.sectors` classification enum extended to include `'trbc'`. Added prose about multi-classification history. |
| T14 | `broker.account_state_snapshot` + `ref.broker_metrics_map` + `broker.nav_daily` + `broker.margin_state_daily` | (rev 11 / F10) Added `metric_canonical` column; added new dim `ref.broker_metrics_map`; rewrote both MVs to filter on `metric_canonical` instead of vendor-native `metric`. |
| T15 | `broker.applied_corporate_actions` DDL (§11.10) | (rev 11 / F11) Added `INDEX idx_security_id security_id TYPE bloom_filter(0.01) GRANULARITY 4` with a comment tying it to the `meta.reconciliation_drift` lookup pattern. |
| T16 | `book.attribution` prose (§7) | (rev 11 / F12) Added a paragraph on open-order attribution — deterministic from `orderRef`, no bridge table, `meta.unresolved_entities` for missing tags. |
| T17 | New §11.14 | (rev 11 / F13) `research.owned_listings` view DDL sketch; not built until Wave 7 tier 3. |
| T18 | `risk.budgets` prose (§8) | (rev 11 / F14) Added a paragraph documenting that position-level stops live in `book.trade_legs.stop_price`, not in `risk.budgets.scope`. |
| T19 | `market.options_bars` DDL (§4) | (rev 11 / F15) Added `-- TODO measure at Wave 5+` comment on `PARTITION BY`. |
| T20 | `alt.political_trades` DDL (§6) | (rev 11 / F16) Added `amount_currency FixedString(3) DEFAULT 'USD'`. |
| T21 | `ref.broker_accounts` + `book.strategies` DDL + §14.1 test table | (rev 11 / F17) Column comments explicit about canonical vs denorm; §14 test category table gains a "Denorm bidirectional consistency" row. |
| T22 | Top-of-doc historical revision blocks | (rev 11 / F18) Italic disclaimer prefix on every revision-summary block (rev 2 through rev 9). |
| T23 | `alt.social_reddit_posts` (§6) | (rev 11 / F19) Deleted the duplicate DDL block (kept the first at line 1256). |
| T24 | `alt.political_filings.bioguide_confidence` (§6) | (rev 11 / F20) Enum spelled out. |
| T25 | `market.bars` conventions prose (§4) | (rev 11 / F21) Extended the never-cross-sum warning to reference `market.volume_by_product_type_daily` (reserved MV name, Wave 5+). |
| T26 | `book.trade_legs.side` → `leg_side` (§7) | (rev 11 / F22) Column rename with comment explaining the disambiguation from `book.trade_names.side`. |
| T27 | Systemic sweep (rev 11) | Normalized every `ref.*` DDL block in §3 to full-syntax `CREATE TABLE (...)`, trailing commas, explicit `ENGINE`, explicit `ORDER BY`, standard audit pair. Full list of tables touched in the rev-11 §18 entry. |

Fixes NOT applied — info findings deliberately left as-is per §19.8:
- **F23** (single `raw.archive` for batch + stream)
- **F24** (`market.trades` / `market.orderbook_l2` reserved names)
- **F25** (`research.owned_listings` / `research.listing_continuous_returns`
  as read-time views — F25 partially superseded: `research.owned_listings`
  now has a DDL sketch under §11.14 per F13; `research.listing_continuous_returns`
  remains a Wave 2 deferral).

### 19.7 Query-walk results

Five archetypal queries traced against the current schema. All resolve
within the "≤ 1–2 joins" principle from §1 except where noted.

| # | Query | Path | Joins | Verdict |
|---|---|---|---|---|
| 1 | All NVDA fills across paper + live accounts in Q3-2026 | `broker.executions` with `WHERE listing_id = nvda AND exec_time IN Q3` | 0 | Pass — bloom index on `listing_id` prunes. |
| 2 | AI-infra theme performance this week | `broker.pnl_by_theme_daily` MV with `WHERE theme_id = ai_infra AND trade_date >= today()-7` | 0 | Pass — MV pre-aggregates. Contract-only today (Wave 8). |
| 3 | Discretionary trade_name target vs actual | `book.trade_legs LEFT JOIN broker.positions_snapshot` on `(listing_id, trade_name_id)` filtered by `trade_name_id` | 1 | Pass — both tables sorted on the leading key. |
| 4 | Senator PTRs from 2025 hitting owned tickers | `alt.political_trades JOIN broker.positions_snapshot ON listing_id` filtered by `broker.positions_snapshot.account_mode='live'` and `alt.political_trades.transaction_date IN 2025` | 1 | Pass with caveat F13 — join loses positions with `NULL listing_id`. |
| 5 | AAPL momentum_12_1 factor 2026-09-15 lineage back to raw bars | `derived.factors → meta.lineage → derived.computations → raw.archive` | 3 hops | Pass — multi-hop but expected per §1 principle "provenance-first, every row can be traced back." |

No query required more joins than principle §1 promises.

### 19.8 Findings resolution log (rev 11)

Every finding from §19.2 through §19.5 is disposed here. Format:
`**F<N>** — resolved by <one-line summary> (rev 11)` or `**F<N>** —
reviewed, no action per finding`.

**F1** — resolved by declaring `ref.entities.entity_type LowCardinality(String)` with a nine-value enum + prose paragraph on its discriminator role (rev 11).
**F2** — resolved by adding an explicit `[blocker]` gate on Wave 8 in §15 and updating §17.1 Q6 to cross-reference the gate (rev 11).
**F3** — resolved by reordering the §2 namespace-layout table to build-wave dependency order (rev 11).
**F4** — resolved by F1 landing + explicit FK comments on `alt.political_committees.committee_entity_id` and `alt.political_committee_memberships.*_entity_id` naming the required `entity_type` (rev 11).
**F5** — resolved by renumbering `§13.13 Canonical FK conventions` → `§13.11`; rev-8 changelog line noting `§13.1-§13.13` fixed inline; new `§13.12` added for F7 so no gap remains (rev 11).
**F6** — resolved by adding `version`, `ingested_at`, `as_of_time`, explicit `ENGINE = ReplacingMergeTree(version)`, `PARTITION BY toYear(event_date)` on `ref.adjustment_factors` + prose on nightly-recompute reproducibility (rev 11).
**F7** — resolved by adding new `§13.12 Denormalization rule: IDs only, not attributes` — codifies the policy `book.attribution` already follows (rev 11).
**F8** — resolved by adding `broker.cash_flows.trade_name_id Nullable(UUID)` for commission/fx_conversion rows + updating §11.12 extensions table (rev 11).
**F9** — resolved by renaming `ref.securities.sector_gics_id` → `sector_id` + adding companion `sector_classification LowCardinality(String)` with a `'trbc'` addition to the `ref.sectors` classification enum + multi-classification prose (rev 11).
**F10** — resolved by adding `broker.account_state_snapshot.metric_canonical` + new `ref.broker_metrics_map` dim + rewriting `broker.nav_daily` and `broker.margin_state_daily` MVs to filter on `metric_canonical` (rev 11).
**F11** — resolved by adding `INDEX idx_security_id security_id TYPE bloom_filter(0.01) GRANULARITY 4` on `broker.applied_corporate_actions` (rev 11).
**F12** — resolved by adding a paragraph after `book.attribution` DDL documenting open-order attribution: deterministic from `orderRef`, no bridge table, `meta.unresolved_entities` for missing tags (rev 11).
**F13** — resolved by adding new `§11.14 research.owned_listings` view DDL sketch; not built until Wave 7 tier 3 (rev 11).
**F14** — resolved by adding a paragraph under `risk.budgets` documenting that position-level stops live in `book.trade_legs.stop_price`, not in `risk.budgets.scope` (rev 11).
**F15** — resolved by adding a `-- TODO measure at Wave 5+` comment on `market.options_bars` `PARTITION BY` (rev 11).
**F16** — resolved by adding `alt.political_trades.amount_currency FixedString(3) DEFAULT 'USD'` (rev 11).
**F17** — resolved by documenting `ref.broker_accounts.strategy_ids` as CANONICAL and `book.strategies.paper_account_ids` / `live_account_ids` as denorm-for-query-speed; §14.1 test-category table gains a "Denorm bidirectional consistency" row (rev 11).
**F18** — resolved by prefixing every historical revision-summary block (rev 2 through rev 9) with an italic "numbering is as-written" disclaimer (rev 11).
**F19** — resolved by deleting the duplicate `alt.social_reddit_posts` DDL block; kept the first occurrence (rev 11).
**F20** — resolved by spelling out the six-value enum on `alt.political_filings.bioguide_confidence` (rev 11).
**F21** — resolved by extending the "never sum across `product_type`" prose to name `market.volume_by_product_type_daily` (reserved MV name, Wave 5+) and to apply the same warning to `market.options_bars.volume` (rev 11).
**F22** — resolved by renaming `book.trade_legs.side` → `book.trade_legs.leg_side` with a comment explaining the disambiguation from `book.trade_names.side` (rev 11).
**F23** — reviewed, no action per finding. Single `raw.archive` handles batch + stream via `window_start_at` / `event_count`; revisit when IBKR streaming or Reddit firehose volume forces a split.
**F24** — reviewed, no action per finding. `market.trades` and `market.orderbook_l2` are reserved names; consistent with §16 deferrals.
**F25** — reviewed, partially superseded. `research.owned_listings` now has a DDL sketch under §11.14 per F13; `research.listing_continuous_returns` remains a Wave 2 deferral, DDL to be authored when the migration wave builds.

---

