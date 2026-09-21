-- Generated from docs/architecture/06-schema-rehau.md.
-- Do not hand-edit: update the design or generator, then regenerate.
-- Wave 5 schema.

CREATE TABLE IF NOT EXISTS fundamentals.filings (
    filing_id          UUID,
    entity_id          UUID,                          -- FK ref.entities
    form_type          LowCardinality(String),        -- '10-K','10-Q','8-K','20-F','40-F','NPORT-P','13F-HR','13F-NT','SC 13G','4','3','5'
    filing_date        Date,                          -- when filed with regulator
    period_end         Nullable(Date),                -- reporting period end (for 10-K/Q)
    period_type        LowCardinality(String),        -- 'annual','quarterly','ytd','ttm','other'
    fiscal_year        Nullable(UInt16),
    fiscal_period      LowCardinality(Nullable(String)),  -- 'Q1','Q2','Q3','Q4','FY'
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

CREATE TABLE IF NOT EXISTS fundamentals.line_items (
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
