-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 7 schema.

CREATE TABLE IF NOT EXISTS broker.positions_snapshot (
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
          vendor_id)
SETTINGS allow_nullable_key = 1;

CREATE TABLE IF NOT EXISTS broker.account_state_snapshot (
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
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(snapshot_time))
ORDER BY (broker_code, account_id, metric, segment, currency, snapshot_time);

CREATE TABLE IF NOT EXISTS broker.executions (
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
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64,
    -- skip index: perm_id joins from the trade engine
    INDEX idx_perm_id       perm_id       TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_listing_id    listing_id    TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(exec_time))
ORDER BY (broker_code, account_id, exec_time, exec_id);

CREATE TABLE IF NOT EXISTS broker.open_orders_snapshot (
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
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64,
    INDEX idx_perm_id       perm_id       TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (broker_code, account_mode, toYYYYMM(snapshot_time))
ORDER BY (broker_code, account_id, snapshot_time, perm_id);

CREATE TABLE IF NOT EXISTS broker.cash_flows (
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
          amount, description)
SETTINGS allow_nullable_key = 1;

CREATE TABLE IF NOT EXISTS broker.applied_corporate_actions (
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

CREATE TABLE IF NOT EXISTS broker.order_events (
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
    prior_state        LowCardinality(Nullable(String)),
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
    route_before       LowCardinality(Nullable(String)),   -- 'SMART' | pinned exchange
    route_after        LowCardinality(Nullable(String)),
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
