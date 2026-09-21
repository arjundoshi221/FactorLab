-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 8 schema.

CREATE TABLE IF NOT EXISTS book.strategies (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (strategy_id);

CREATE TABLE IF NOT EXISTS book.themes (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (theme_id);

CREATE TABLE IF NOT EXISTS book.trade_names (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (trade_name_id);

CREATE TABLE IF NOT EXISTS book.trade_legs (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(effective_from)
ORDER BY (trade_name_id, listing_id, effective_from);

CREATE TABLE IF NOT EXISTS book.attribution (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(assigned_at)
ORDER BY (trade_name_id, execution_id, assigned_at);

CREATE TABLE IF NOT EXISTS risk.budgets (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
PARTITION BY (scope, toYYYYMM(effective_from))
ORDER BY (scope, scope_id, effective_from);
