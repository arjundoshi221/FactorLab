-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 4 schema.

CREATE TABLE IF NOT EXISTS alt.political_trades (
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
ORDER BY (country_code, chamber, transaction_date, entity_id, political_trade_id)
SETTINGS allow_nullable_key = 1;

CREATE TABLE IF NOT EXISTS alt.political_filings (
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
    source                   LowCardinality(String),
    source_channel           LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (chamber, filing_year)
ORDER BY (country_code, chamber, filing_id);

CREATE TABLE IF NOT EXISTS alt.political_committee_memberships (
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
    source                   LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (country_code, congress_number, committee_id, bioguide_id, effective_from);

CREATE TABLE IF NOT EXISTS alt.political_committees (
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
    source                   LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (committee_id, effective_from);

CREATE TABLE IF NOT EXISTS alt.social_reddit_posts (
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
    source                   LowCardinality(String),
    raw_id                   Nullable(UUID),
    ingest_run_id            UUID,
    as_of_time               DateTime64(3, 'UTC'),
    ingested_at              DateTime64(3, 'UTC'),
    version                  UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(posted_at)
ORDER BY (country_code, subreddit, posted_at, post_id);
