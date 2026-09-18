-- FactorLab ClickHouse political reference data and House STOCK Act filings.

CREATE TABLE IF NOT EXISTS alt_political_legislators (
    bioguide_id String,
    first_name String,
    middle_name Nullable(String),
    last_name String,
    suffix Nullable(String),
    official_full String,
    birthday Nullable(Date32),
    gender LowCardinality(String),
    chamber LowCardinality(String),
    state FixedString(2),
    district Nullable(UInt16),
    party LowCardinality(String),
    term_start Date,
    term_end Date,
    in_office Bool,
    fec_candidate_ids Array(String),
    govtrack_id Nullable(UInt32),
    opensecrets_id Nullable(String),
    source LowCardinality(String),
    raw_id UUID,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY bioguide_id;

CREATE TABLE IF NOT EXISTS alt_political_committees (
    committee_id String,
    parent_committee_id Nullable(String),
    chamber LowCardinality(String),
    name String,
    jurisdiction String,
    url String,
    is_subcommittee Bool,
    is_current Bool,
    source LowCardinality(String),
    raw_id UUID,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY committee_id;

CREATE TABLE IF NOT EXISTS alt_political_committee_memberships (
    snapshot_date Date,
    congress_number UInt16,
    committee_id String,
    committee_name String,
    bioguide_id String,
    member_name String,
    political_party LowCardinality(String),
    state FixedString(2),
    majority_status LowCardinality(String),
    role LowCardinality(String),
    role_raw String,
    rank UInt16,
    source LowCardinality(String),
    raw_id UUID,
    version UInt64,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(snapshot_date)
ORDER BY (snapshot_date, committee_id, bioguide_id);

CREATE TABLE IF NOT EXISTS alt_political_house_filings (
    filing_id String,
    filing_year UInt16,
    filing_type LowCardinality(String),
    filing_date Date,
    filer_prefix String,
    filer_first_name String,
    filer_last_name String,
    filer_suffix String,
    filer_name_raw String,
    bioguide_id Nullable(String),
    state FixedString(2),
    district Nullable(UInt16),
    state_district_raw String,
    filing_url String,
    source LowCardinality(String),
    raw_id UUID,
    version UInt64,
    as_of_time DateTime64(3, 'UTC'),
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY filing_year
ORDER BY filing_id;

CREATE TABLE IF NOT EXISTS alt_political_trades (
    trade_key FixedString(64),
    country_code FixedString(2),
    chamber LowCardinality(String),
    filing_id String,
    filing_year UInt16,
    filing_date Date,
    filing_url String,
    bioguide_id Nullable(String),
    legislator_name String,
    state FixedString(2),
    district Nullable(UInt16),
    owner_code LowCardinality(String),
    filer_type LowCardinality(String),
    asset_name_raw String,
    ticker Nullable(String),
    asset_type_code LowCardinality(String),
    transaction_type LowCardinality(String),
    transaction_date Date,
    notification_date Nullable(Date),
    amount_str String,
    amount_min Nullable(UInt64),
    amount_max Nullable(UInt64),
    source LowCardinality(String),
    raw_id UUID,
    parser_version LowCardinality(String),
    as_of_time DateTime64(3, 'UTC'),
    ingested_at DateTime64(3, 'UTC'),
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(transaction_date)
ORDER BY (trade_key, transaction_date);
