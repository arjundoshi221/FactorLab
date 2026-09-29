-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 1 schema.

CREATE TABLE IF NOT EXISTS ref.countries (
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

CREATE TABLE IF NOT EXISTS ref.currencies (
    currency_code      FixedString(3),           -- ISO-4217
    name               String,
    is_deliverable     Bool,                     -- 'INR' is non-deliverable offshore
    version            UInt64,
    ingested_at        DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY currency_code;

CREATE TABLE IF NOT EXISTS ref.exchanges (
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

CREATE TABLE IF NOT EXISTS ref.sectors (
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

CREATE TABLE IF NOT EXISTS ref.sources (
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

CREATE TABLE IF NOT EXISTS ref.execution_methods (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (method_id, valid_from);

CREATE TABLE IF NOT EXISTS ref.broker_metrics_map (
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

CREATE TABLE IF NOT EXISTS ref.entities (
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

CREATE TABLE IF NOT EXISTS ref.entity_relationships (
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

CREATE TABLE IF NOT EXISTS ref.securities (
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

CREATE TABLE IF NOT EXISTS ref.listings (
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

CREATE TABLE IF NOT EXISTS ref.contracts (
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
ORDER BY (underlying_listing_id, expiry, right, strike)
SETTINGS allow_nullable_key = 1;

CREATE TABLE IF NOT EXISTS ref.broker_accounts (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (broker_code, account_id);

CREATE TABLE IF NOT EXISTS ref.identifier_aliases (
    alias_kind            LowCardinality(String),           -- 'ticker','isin','cusip','figi','lei',
                                                            --   'sedol','openfigi','bloomberg',
                                                            --   'schwab_conid','ibkr_conid','eodhd_symbol',
                                                            --   'upstox_instrument_key','fec_candidate_id',
                                                            --   'bioguide','cik','ric','permid'
    alias_value           String,
    scope_country         Nullable(FixedString(2)),         -- ticker namespaces are country-scoped
    scope_exchange        LowCardinality(Nullable(String)), -- for MIC-scoped tickers
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

CREATE TABLE IF NOT EXISTS ref.listing_migrations (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (old_listing_id, effective_date, migration_id);

CREATE TABLE IF NOT EXISTS ref.corporate_actions (
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

CREATE TABLE IF NOT EXISTS ref.adjustment_factors (
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

-- Universe definitions
CREATE TABLE IF NOT EXISTS ref.universes (
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
CREATE TABLE IF NOT EXISTS ref.universe_membership (
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

CREATE TABLE IF NOT EXISTS ref.dataset_versions (
    dataset            LowCardinality(String),      -- 'market.bars_adjusted','fundamentals.snapshots_pit_eom',
                                                     --   'ref.universe_membership.r3k','ref.adjustment_factors'
    version_tag        LowCardinality(String),      -- 'v1','v2','v2.1'
    superseded_by      LowCardinality(Nullable(String)),
    materialized_from  DateTime64(3, 'UTC'),        -- when this version's data starts
    materialized_to    Nullable(DateTime64(3, 'UTC')),
    methodology_note   String,                       -- what changed vs prior version
    computation_id     Nullable(UUID),               -- FK derived.computations, if it was a full recompute
    active             Bool,
    created_at         DateTime64(3, 'UTC'),
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (dataset, version_tag, materialized_from);

CREATE TABLE IF NOT EXISTS ref.legislator_terms (
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
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)
ORDER BY (bioguide_id, term_start);

CREATE TABLE IF NOT EXISTS ref.sessions (
    exchange_code  LowCardinality(String),
    session        LowCardinality(String),      -- 'pre','regular','post','open_auction','close_auction'
    day_of_week    UInt8,                        -- 0-6
    starts_at      String,                       -- 'HH:MM' local
    ends_at        String,
    effective_from Date,
    effective_to   Nullable(Date),
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')                         -- (added rev 10 for consistency with sibling dims)
) ENGINE = ReplacingMergeTree(version)
ORDER BY (exchange_code, effective_from, session);

CREATE TABLE IF NOT EXISTS ref.holidays (
    country_code   FixedString(2),
    exchange_code  LowCardinality(Nullable(String)),  -- exchange-specific holidays
    holiday_date   Date,
    name           String,
    market_state   LowCardinality(String),      -- 'closed','early_close','late_open',
                                                 --   'muhurat_only','open_only'
    override_open  Nullable(String),             -- 'HH:MM' local, if late open
    override_close Nullable(String),             -- 'HH:MM' local, if early close
    override_pre_close Nullable(String),
    override_post_open Nullable(String),
    source         LowCardinality(String),
    version                  UInt64,
    ingested_at              DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(version)                 -- (added rev 10: was missing)
ORDER BY (country_code, holiday_date);

CREATE TABLE IF NOT EXISTS raw.archive (
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
SETTINGS storage_policy = 'raw_archive', allow_nullable_key = 1;
