"""alt_political schema — US Congress STOCK-Act trades, lobbying, contracts,
campaign donations, bills, hearings.

Designed to 3NF (BCNF where natural). The schema separates:

    DIMENSIONS (slow-changing, ~1 row per real-world entity):
        legislators                   bioguide_id keyed
        legislator_terms              SCD2 by term_start
        legislator_fec_ids            many-to-one (multiple campaign IDs per person)
        committees                    Thomas-ID keyed (current + historical)
        committee_assignments         (committee × bioguide × congress) with role
        committee_sector_map          (committee × GICS) signal-strength matrix
        asset_type_codes              48 House Clerk codes (ST, OP, GS, ...)
        lda_issue_codes               79 LDA issue codes (DEF, HCR, TAX, ...)
        lda_government_entities       257 lobbyable agencies/chambers
        fec_committees                FEC PAC + campaign committee dim
        contract_aliases              recipient-name → ticker resolution cache
        lobby_client_aliases          LDA-client-name → ticker resolution cache

    EVENTS (fact tables, append-only):
        legislator_trades             PTRs from House Clerk + Senate eFD
        gov_contracts                 USASpending awards (sovereign + Finnhub)
        lobbying_filings              LDA quarterly filings
        lobbying_activities           per-filing per-issue rows
        lobbying_activity_targets     M:N activity ↔ government_entity
        lobbying_activity_lobbyists   M:N activity ↔ person (revolving-door)
        campaign_donations            FEC schedule_a contributions
        bills                         Congress.gov bills
        bill_sponsors                 M:N bill ↔ legislator (sponsor/cosponsor)
        bill_committees               M:N bill ↔ committee with activity_date
        bill_actions                  per-bill action history (markup, vote, etc.)
        hearings                      committee hearings
        hearing_witnesses             per-hearing witness list
        raw_archive                   raw payload audit trail (gzipped bodies)

Country tagging: every dim + event carries country_code FK to ref.countries.code.
v1 seeds with 'US'. Future jurisdictions reuse the same shape.

Join surface is via two stable keys:
    bioguide_id   — every legislator-side join
    ticker        — every company-side join (denormalized into events for speed)

Both are nullable on event tables until resolution succeeds; partial resolution
must not block ingest.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from factorlab.storage.db import metadata
from factorlab.storage.schemas._columns import col_created_at, col_updated_at

SCHEMA = "alt_political"
COUNTRY_FK = "ref.countries.code"
INSTRUMENT_FK = "ref.instruments.id"

# ===========================================================================
#  DIMENSION TABLES
# ===========================================================================

# ---------------------------------------------------------------------------
# alt_political.legislators — one row per real human (bioguide_id PK)
# ---------------------------------------------------------------------------
legislators = Table(
    "legislators",
    metadata,
    Column("bioguide_id", String(7), primary_key=True,
           comment="Library of Congress bioguide.congress.gov ID — canonical FK"),
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), nullable=False, server_default="'US'"),
    Column("first_name", String(80), nullable=False),
    Column("middle_name", String(80), nullable=True),
    Column("last_name", String(80), nullable=False),
    Column("suffix", String(20), nullable=True),
    Column("nickname", String(80), nullable=True),
    Column("official_full", String(200), nullable=True),
    Column("gender", String(1), nullable=True),
    Column("birthday", Date, nullable=True),
    # Cross-system IDs (single-valued, all-time stable)
    Column("thomas_id", String(8), nullable=True),
    Column("govtrack_id", Integer, nullable=True),
    Column("opensecrets_cid", String(20), nullable=True),
    Column("wikipedia_id", String(200), nullable=True),
    Column("ballotpedia_id", String(200), nullable=True),
    Column("fec_candidate_id_primary", String(9), nullable=True,
           comment="Most recent / primary FEC candidate id; full set in legislator_fec_ids"),
    Column("in_office", Boolean, nullable=False, server_default="false"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)
Index("ix_legislators_country_last_first",
      legislators.c.country_code, legislators.c.last_name, legislators.c.first_name)
Index("ix_legislators_in_office", legislators.c.in_office)


# ---------------------------------------------------------------------------
# alt_political.legislator_terms — SCD: chamber/state/district/party per term
# ---------------------------------------------------------------------------
legislator_terms = Table(
    "legislator_terms",
    metadata,
    Column("bioguide_id", String(7),
           ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
           primary_key=True),
    Column("term_start", Date, primary_key=True),
    Column("term_end", Date, nullable=False),
    Column("chamber", String(3), nullable=False, comment="'sen' | 'rep'"),
    Column("state", String(2), nullable=False),
    Column("district", Integer, nullable=True, comment="House only; senators NULL"),
    Column("party", String(20), nullable=True),
    Column("congress_number", Integer, nullable=True,
           comment="Derived: Congress # active during this term (118, 119, ...)"),
    CheckConstraint("chamber IN ('sen','rep')", name="legislator_terms_chamber_valid"),
    CheckConstraint("term_end >= term_start", name="legislator_terms_end_after_start"),
    col_created_at(),
    schema=SCHEMA,
)
Index("ix_legislator_terms_chamber_state",
      legislator_terms.c.chamber, legislator_terms.c.state)
Index("ix_legislator_terms_dates",
      legislator_terms.c.term_start, legislator_terms.c.term_end)


# ---------------------------------------------------------------------------
# alt_political.legislator_fec_ids — multiple FEC IDs per real person
# ---------------------------------------------------------------------------
legislator_fec_ids = Table(
    "legislator_fec_ids",
    metadata,
    Column("fec_candidate_id", String(9), primary_key=True,
           comment="FEC candidate id, e.g. H8CA05035, S4MD00033"),
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), nullable=False, server_default="'US'"),
    Column("bioguide_id", String(7),
           ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
           nullable=False),
    Column("office", String(1), nullable=False, comment="'H' | 'S' | 'P'"),
    Column("state", String(2), nullable=True),
    Column("district", Integer, nullable=True),
    Column("first_election_year", Integer, nullable=True),
    CheckConstraint("office IN ('H','S','P')", name="legislator_fec_ids_office_valid"),
    col_created_at(),
    schema=SCHEMA,
)
Index("ix_legislator_fec_ids_bioguide", legislator_fec_ids.c.bioguide_id)


# ---------------------------------------------------------------------------
# alt_political.committees — committee dimension (current + historical)
# Composite PK includes country_code so future jurisdictions don't collide.
# ---------------------------------------------------------------------------
committees = Table(
    "committees",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("committee_id", String(8), primary_key=True,
           comment="Thomas ID: SSFI, HSAS, SSAS01 (subcommittee)"),
    Column("chamber", String(6), nullable=False, comment="'senate' | 'house' | 'joint'"),
    Column("name", String(300), nullable=False),
    # FK to parent committee — composite to match the parent's PK
    Column("parent_country_code", String(2), nullable=True),
    Column("parent_committee_id", String(8), nullable=True,
           comment="NULL for full committees; FK for subcommittees"),
    Column("jurisdiction", Text, nullable=True),
    Column("url", String(300), nullable=True),
    Column("rss_url", String(300), nullable=True),
    Column("is_current", Boolean, nullable=False, server_default="true"),
    ForeignKeyConstraint(
        ["parent_country_code", "parent_committee_id"],
        [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
        name="fk_committees_parent",
    ),
    CheckConstraint("chamber IN ('senate','house','joint')", name="committees_chamber_valid"),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)
Index("ix_committees_chamber", committees.c.chamber)
Index("ix_committees_parent",
      committees.c.parent_country_code, committees.c.parent_committee_id)


# ---------------------------------------------------------------------------
# alt_political.committee_assignments — point-in-time committee membership
# ---------------------------------------------------------------------------
committee_assignments = Table(
    "committee_assignments",
    metadata,
    Column("country_code", String(2), primary_key=True, server_default="'US'"),
    Column("committee_id", String(8), primary_key=True),
    Column("bioguide_id", String(7),
           ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
           primary_key=True),
    Column("congress_number", Integer, primary_key=True, comment="118, 119, ..."),
    Column("role", String(30), nullable=False,
           comment="'chair' | 'ranking_member' | 'member' | 'vice_chair' | 'ex_officio' | ..."),
    Column("rank", Integer, nullable=True),
    Column("valid_from", Date, nullable=False),
    Column("valid_to", Date, nullable=True, comment="NULL = current"),
    ForeignKeyConstraint(
        ["country_code", "committee_id"],
        [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
        name="fk_committee_assignments_committee", ondelete="CASCADE",
    ),
    CheckConstraint(
        "role IN ('chair','ranking_member','member','vice_chair','ex_officio',"
        "'chairwoman','cochairman','vice_chairman','vice_chairwoman')",
        name="committee_assignments_role_valid",
    ),
    col_created_at(),
    schema=SCHEMA,
)
Index("ix_committee_assignments_bioguide", committee_assignments.c.bioguide_id)
Index("ix_committee_assignments_dates",
      committee_assignments.c.valid_from, committee_assignments.c.valid_to)


# ---------------------------------------------------------------------------
# alt_political.committee_sector_map — sectoral relevance per committee
# ---------------------------------------------------------------------------
committee_sector_map = Table(
    "committee_sector_map",
    metadata,
    Column("country_code", String(2), primary_key=True, server_default="'US'"),
    Column("committee_id", String(8), primary_key=True),
    Column("gics_code", String(10), primary_key=True,
           comment="GICS or NAICS-prefix sector key"),
    Column("signal_strength", String(15), nullable=False,
           comment="'extreme' | 'very_high' | 'high' | 'medium_high' | 'medium' | 'low'"),
    Column("rationale", Text, nullable=True),
    ForeignKeyConstraint(
        ["country_code", "committee_id"],
        [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
        name="fk_committee_sector_map_committee", ondelete="CASCADE",
    ),
    CheckConstraint(
        "signal_strength IN ('extreme','very_high','high','medium_high','medium','low')",
        name="committee_sector_map_strength_valid",
    ),
    col_created_at(),
    schema=SCHEMA,
)


# ---------------------------------------------------------------------------
# alt_political.asset_type_codes — House Clerk's 48 codes
# ---------------------------------------------------------------------------
asset_type_codes = Table(
    "asset_type_codes",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("code", String(2), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("equity_ticker_likely", Boolean, nullable=False, server_default="false",
           comment="True for ST/EF/ET/OP/RS/SA — ticker expected in raw asset name"),
    col_created_at(),
    schema=SCHEMA,
)


# ---------------------------------------------------------------------------
# alt_political.lda_issue_codes — 79 LDA issue codes
# ---------------------------------------------------------------------------
lda_issue_codes = Table(
    "lda_issue_codes",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("code", String(3), primary_key=True),
    Column("name", String(200), nullable=False),
    col_created_at(),
    schema=SCHEMA,
)


# ---------------------------------------------------------------------------
# alt_political.lda_government_entities — 257 lobbyable orgs
# ---------------------------------------------------------------------------
lda_government_entities = Table(
    "lda_government_entities",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("entity_id", Integer, primary_key=True, comment="LDA's numeric id"),
    Column("name", String(300), nullable=False),
    col_created_at(),
    schema=SCHEMA,
)


# ---------------------------------------------------------------------------
# alt_political.fec_committees — FEC PACs + campaign committees
# ---------------------------------------------------------------------------
fec_committees = Table(
    "fec_committees",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("committee_id", String(9), primary_key=True,
           comment="FEC committee id, e.g. C00303024"),
    Column("name", String(300), nullable=False),
    Column("committee_type", String(2), nullable=True,
           comment="Q=Qualified PAC, P=Party, N=Non-Qualified PAC, S=Senate, H=House, ..."),
    Column("committee_type_full", String(80), nullable=True),
    Column("designation", String(2), nullable=True),
    Column("organization_type", String(2), nullable=True),
    Column("sponsor_company_ticker", String(10), nullable=True,
           comment="Resolved ticker if corporate PAC; NULL for party/leadership/super-PACs"),
    Column("first_file_date", Date, nullable=True),
    Column("last_file_date", Date, nullable=True),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)
Index("ix_fec_committees_ticker", fec_committees.c.sponsor_company_ticker)
Index("ix_fec_committees_type", fec_committees.c.committee_type)


# ---------------------------------------------------------------------------
# alt_political.contract_aliases — recipient_name → ticker resolution
# ---------------------------------------------------------------------------
contract_aliases = Table(
    "contract_aliases",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("normalized_name", String(300), primary_key=True,
           comment="Lowercased, suffix-stripped name"),
    Column("ticker", String(10), nullable=False),
    Column("confidence", Numeric(3, 2), nullable=False, server_default="1.00"),
    Column("source", String(20), nullable=False,
           comment="'manual' | 'exact' | 'prefix' | 'fuzzy' | 'learned' | 'finnhub_xref'"),
    Column("first_seen_name", String(300), nullable=True,
           comment="First raw name that matched this normalized form"),
    CheckConstraint(
        "source IN ('manual','exact','prefix','fuzzy','learned','finnhub_xref')",
        name="contract_aliases_source_valid",
    ),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)
Index("ix_contract_aliases_ticker", contract_aliases.c.ticker)


# ---------------------------------------------------------------------------
# alt_political.lobby_client_aliases — LDA client_name → ticker
# ---------------------------------------------------------------------------
lobby_client_aliases = Table(
    "lobby_client_aliases",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("normalized_name", String(300), primary_key=True),
    Column("ticker", String(10), nullable=False),
    Column("confidence", Numeric(3, 2), nullable=False, server_default="1.00"),
    Column("source", String(20), nullable=False),
    CheckConstraint(
        "source IN ('manual','exact','prefix','fuzzy','learned')",
        name="lobby_client_aliases_source_valid",
    ),
    col_created_at(),
    col_updated_at(),
    schema=SCHEMA,
)
Index("ix_lobby_client_aliases_ticker", lobby_client_aliases.c.ticker)


# ===========================================================================
#  EVENT / FACT TABLES
# ===========================================================================

# ---------------------------------------------------------------------------
# alt_political.legislator_trades — STOCK Act PTRs (House + Senate)
# ---------------------------------------------------------------------------
legislator_trades = Table(
    "legislator_trades",
    metadata,
    Column("trade_id", UUID(as_uuid=True), primary_key=True,
           server_default=text("gen_random_uuid()")),
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), nullable=False, server_default="'US'"),
    Column("chamber", String(6), nullable=False),
    Column("bioguide_id", String(7),
           ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="SET NULL"),
           nullable=True,
           comment="NULL when filer fuzzy-match failed"),
    Column("legislator_name_raw", String(200), nullable=False,
           comment="Exactly as filed; preserved for audit + re-resolution"),
    # Filing identity
    Column("source", String(40), nullable=False,
           comment="'house_clerk_ptr' | 'senate_efd_ptr' | 'senate_stock_watcher_historical'"),
    Column("filing_id", String(50), nullable=False,
           comment="DocID (House) | UUID (Senate eFD) | composite hash (SSW)"),
    Column("filing_date", Date, nullable=False),
    Column("filing_url", String(500), nullable=True),
    # Transaction
    Column("transaction_date", Date, nullable=False),
    Column("notification_date", Date, nullable=True,
           comment="House-only field; Senate doesn't surface it"),
    Column("filer_type", String(20), nullable=False,
           comment="'self' | 'spouse' | 'dependent_child' | 'joint' | 'junior'"),
    Column("transaction_type", String(20), nullable=False,
           comment="'purchase' | 'sale_full' | 'sale_partial' | 'exchange'"),
    # Asset
    Column("asset_name_raw", Text, nullable=False),
    Column("asset_type_code", String(2), nullable=True,
           comment="House uses 2-char codes; Senate uses words mapped to same codes"),
    Column("ticker", String(10), nullable=True,
           comment="Denorm cache for fast joins; ground truth via security_id"),
    Column("security_id", UUID(as_uuid=True),
           ForeignKey(INSTRUMENT_FK, ondelete="SET NULL"), nullable=True),
    # Amount (STOCK Act bands; never exact)
    Column("amount_str", String(40), nullable=False, comment="Original bucket string"),
    Column("amount_min", BigInteger, nullable=True),
    Column("amount_max", BigInteger, nullable=True,
           comment="NULL when bucket is 'Over $X'"),
    Column("amount_mid", BigInteger, nullable=True,
           comment="(min+max)/2 for modeling; min when max NULL"),
    # Audit
    Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("as_of_time", DateTime(timezone=True), nullable=False, server_default=func.now(),
           comment="Point-in-time: when WE knew this fact"),
    ForeignKeyConstraint(
        ["country_code", "asset_type_code"],
        [f"{SCHEMA}.asset_type_codes.country_code", f"{SCHEMA}.asset_type_codes.code"],
        name="fk_legislator_trades_asset_type",
    ),
    UniqueConstraint(
        "country_code", "chamber", "filing_id", "transaction_date",
        "asset_name_raw", "transaction_type", "amount_str",
        name="uq_legislator_trades_dedup",
    ),
    CheckConstraint("chamber IN ('senate','house')", name="legislator_trades_chamber_valid"),
    CheckConstraint(
        "transaction_type IN ('purchase','sale_full','sale_partial','exchange')",
        name="legislator_trades_tx_type_valid",
    ),
    CheckConstraint(
        "filer_type IN ('self','spouse','dependent_child','joint','junior')",
        name="legislator_trades_filer_type_valid",
    ),
    schema=SCHEMA,
)
Index("ix_legislator_trades_bioguide_date",
      legislator_trades.c.bioguide_id, legislator_trades.c.transaction_date)
Index("ix_legislator_trades_ticker_date",
      legislator_trades.c.ticker, legislator_trades.c.transaction_date)
Index("ix_legislator_trades_filing_date", legislator_trades.c.filing_date.desc())
Index("ix_legislator_trades_country_filing",
      legislator_trades.c.country_code, legislator_trades.c.filing_id)


# ---------------------------------------------------------------------------
# alt_political.gov_contracts — federal contract awards
# ---------------------------------------------------------------------------
gov_contracts = Table(
    "gov_contracts",
    metadata,
    Column("contract_id", String(120), primary_key=True,
           comment="USASpending generated_internal_id, e.g. CONT_AWD_xxx_yyy"),
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), nullable=False, server_default="'US'"),
    Column("award_id", String(60), nullable=True, comment="PIID / FAIN"),
    Column("recipient_legal_name", String(300), nullable=False),
    Column("recipient_parent_name", String(300), nullable=True),
    Column("recipient_uei", String(12), nullable=True),
    Column("recipient_id", String(50), nullable=True),
    Column("ticker", String(10), nullable=True),
    Column("security_id", UUID(as_uuid=True),
           ForeignKey(INSTRUMENT_FK, ondelete="SET NULL"), nullable=True),
    Column("total_value", Numeric(20, 2), nullable=True),
    Column("obligated_amount", Numeric(20, 2), nullable=True),
    Column("outlayed_amount", Numeric(20, 2), nullable=True),
    Column("potential_amount", Numeric(20, 2), nullable=True),
    Column("action_date", Date, nullable=True),
    Column("performance_start_date", Date, nullable=True),
    Column("performance_end_date", Date, nullable=True),
    Column("awarding_agency", String(200), nullable=True),
    Column("awarding_sub_agency", String(200), nullable=True),
    Column("awarding_office", String(200), nullable=True),
    Column("performance_state", String(2), nullable=True),
    Column("performance_country", String(3), nullable=True),
    Column("performance_district", String(8), nullable=True,
           comment="e.g. 'NY-22' — joins to House district"),
    Column("description", Text, nullable=True),
    Column("naics_code", String(10), nullable=True),
    Column("permalink", String(500), nullable=True),
    Column("source", String(30), nullable=False,
           comment="'usaspending_direct' | 'finnhub_usa_spending'"),
    Column("source_recipient_query", String(300), nullable=True,
           comment="The recipient_name we queried with"),
    Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_modified_date", Date, nullable=True),
    CheckConstraint(
        "source IN ('usaspending_direct','finnhub_usa_spending')",
        name="gov_contracts_source_valid",
    ),
    schema=SCHEMA,
)
Index("ix_gov_contracts_ticker_action_date",
      gov_contracts.c.ticker, gov_contracts.c.action_date.desc())
Index("ix_gov_contracts_awarding_agency", gov_contracts.c.awarding_agency)
Index("ix_gov_contracts_naics", gov_contracts.c.naics_code)
Index("ix_gov_contracts_district", gov_contracts.c.performance_district)


# ---------------------------------------------------------------------------
# alt_political.lobbying_filings — LDA quarterly filings
# ---------------------------------------------------------------------------
lobbying_filings = Table(
    "lobbying_filings",
    metadata,
    Column("filing_uuid", UUID(as_uuid=True), primary_key=True,
           comment="LDA filing_uuid (their canonical PK)"),
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), nullable=False, server_default="'US'"),
    Column("filing_type", String(4), nullable=False,
           comment="'RR' | 'MM' | 'Q1'-'Q4' | 'RA' | 'TR' | ..."),
    Column("filing_year", Integer, nullable=False),
    Column("filing_period", String(20), nullable=True),
    Column("client_name", String(300), nullable=False),
    Column("client_id", Integer, nullable=True),
    Column("client_ticker", String(10), nullable=True),
    Column("registrant_name", String(300), nullable=False),
    Column("registrant_id", Integer, nullable=True),
    Column("income", Numeric(15, 2), nullable=True,
           comment="Fee paid TO registrant BY client"),
    Column("expenses", Numeric(15, 2), nullable=True,
           comment="Client's in-house lobbying expense"),
    Column("dt_posted", DateTime(timezone=True), nullable=True),
    Column("filing_document_url", String(500), nullable=True),
    Column("termination_date", Date, nullable=True),
    Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema=SCHEMA,
)
Index("ix_lobbying_filings_client_ticker_year",
      lobbying_filings.c.client_ticker, lobbying_filings.c.filing_year)
Index("ix_lobbying_filings_client_name", lobbying_filings.c.client_name)
Index("ix_lobbying_filings_registrant_name", lobbying_filings.c.registrant_name)
Index("ix_lobbying_filings_year_period",
      lobbying_filings.c.filing_year, lobbying_filings.c.filing_period)


# ---------------------------------------------------------------------------
# alt_political.lobbying_activities — per-filing per-issue rows
# ---------------------------------------------------------------------------
lobbying_activities = Table(
    "lobbying_activities",
    metadata,
    Column("activity_id", UUID(as_uuid=True), primary_key=True,
           server_default=text("gen_random_uuid()")),
    Column("filing_uuid", UUID(as_uuid=True),
           ForeignKey(f"{SCHEMA}.lobbying_filings.filing_uuid", ondelete="CASCADE"),
           nullable=False),
    Column("country_code", String(2), nullable=False, server_default="'US'"),
    Column("issue_code", String(3), nullable=True),
    Column("description", Text, nullable=True,
           comment="Free-text — bills referenced, regulatory targets"),
    Column("foreign_entity_issues", Text, nullable=True),
    ForeignKeyConstraint(
        ["country_code", "issue_code"],
        [f"{SCHEMA}.lda_issue_codes.country_code", f"{SCHEMA}.lda_issue_codes.code"],
        name="fk_lobbying_activities_issue",
    ),
    schema=SCHEMA,
)
Index("ix_lobbying_activities_filing", lobbying_activities.c.filing_uuid)
Index("ix_lobbying_activities_issue_code", lobbying_activities.c.issue_code)


# ---------------------------------------------------------------------------
# alt_political.lobbying_activity_targets — M:N activity ↔ government_entity
# ---------------------------------------------------------------------------
lobbying_activity_targets = Table(
    "lobbying_activity_targets",
    metadata,
    Column("activity_id", UUID(as_uuid=True),
           ForeignKey(f"{SCHEMA}.lobbying_activities.activity_id", ondelete="CASCADE"),
           primary_key=True),
    Column("country_code", String(2), primary_key=True, server_default="'US'"),
    Column("entity_id", Integer, primary_key=True),
    ForeignKeyConstraint(
        ["country_code", "entity_id"],
        [f"{SCHEMA}.lda_government_entities.country_code",
         f"{SCHEMA}.lda_government_entities.entity_id"],
        name="fk_lobbying_activity_targets_entity",
    ),
    schema=SCHEMA,
)


# ---------------------------------------------------------------------------
# alt_political.lobbying_activity_lobbyists — M:N activity ↔ person
# ---------------------------------------------------------------------------
lobbying_activity_lobbyists = Table(
    "lobbying_activity_lobbyists",
    metadata,
    Column("activity_id", UUID(as_uuid=True),
           ForeignKey(f"{SCHEMA}.lobbying_activities.activity_id", ondelete="CASCADE"),
           primary_key=True),
    Column("lobbyist_id", Integer, primary_key=True,
           comment="LDA lobbyist id when present; else 0 with composite name match"),
    Column("last_name", String(80), primary_key=True),
    Column("first_name", String(80), primary_key=True),
    Column("middle_name", String(80), nullable=True),
    Column("suffix", String(20), nullable=True),
    Column("covered_position", Text, nullable=True,
           comment="Prior gov role (revolving-door indicator)"),
    Column("is_new", Boolean, nullable=False, server_default="false"),
    schema=SCHEMA,
)
Index("ix_lobbying_activity_lobbyists_name",
      lobbying_activity_lobbyists.c.last_name, lobbying_activity_lobbyists.c.first_name)


# ---------------------------------------------------------------------------
# alt_political.campaign_donations — FEC schedule_a contributions
# ---------------------------------------------------------------------------
campaign_donations = Table(
    "campaign_donations",
    metadata,
    Column("donation_id", UUID(as_uuid=True), primary_key=True,
           server_default=text("gen_random_uuid()")),
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), nullable=False, server_default="'US'"),
    Column("sub_id", String(50), nullable=True, comment="FEC sub_id when available"),
    Column("cycle", Integer, nullable=False,
           comment="Two-year transaction period (2024 = 2023-2024)"),
    Column("donor_name", String(200), nullable=True),
    Column("donor_employer", String(200), nullable=True),
    Column("donor_occupation", String(200), nullable=True),
    Column("donor_state", String(2), nullable=True),
    Column("donor_zip", String(10), nullable=True),
    Column("donor_city", String(100), nullable=True),
    Column("donor_committee_country", String(2), nullable=True),
    Column("donor_committee_id", String(9), nullable=True,
           comment="When donor IS a committee (PAC giving to candidate)"),
    Column("amount", Numeric(15, 2), nullable=False),
    Column("date", Date, nullable=True),
    Column("transaction_type", String(10), nullable=True),
    Column("recipient_committee_country", String(2), nullable=True),
    Column("recipient_committee_id", String(9), nullable=True),
    Column("recipient_committee_name", String(300), nullable=True),
    Column("candidate_id", String(9),
           ForeignKey(f"{SCHEMA}.legislator_fec_ids.fec_candidate_id", ondelete="SET NULL"),
           nullable=True),
    Column("candidate_name", String(200), nullable=True),
    Column("filing_url", String(500), nullable=True),
    Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["donor_committee_country", "donor_committee_id"],
        [f"{SCHEMA}.fec_committees.country_code", f"{SCHEMA}.fec_committees.committee_id"],
        name="fk_campaign_donations_donor_committee", ondelete="SET NULL",
    ),
    ForeignKeyConstraint(
        ["recipient_committee_country", "recipient_committee_id"],
        [f"{SCHEMA}.fec_committees.country_code", f"{SCHEMA}.fec_committees.committee_id"],
        name="fk_campaign_donations_recipient_committee", ondelete="SET NULL",
    ),
    UniqueConstraint("sub_id", name="uq_campaign_donations_sub_id"),
    schema=SCHEMA,
)
Index("ix_campaign_donations_recipient_date",
      campaign_donations.c.recipient_committee_id, campaign_donations.c.date.desc())
Index("ix_campaign_donations_donor_employer_cycle",
      campaign_donations.c.donor_employer, campaign_donations.c.cycle)
Index("ix_campaign_donations_donor_committee",
      campaign_donations.c.donor_committee_id)
Index("ix_campaign_donations_candidate", campaign_donations.c.candidate_id)


# ---------------------------------------------------------------------------
# alt_political.bills — Congress.gov bills
# ---------------------------------------------------------------------------
bills = Table(
    "bills",
    metadata,
    Column("bill_uid", String(40), primary_key=True,
           comment="'{country}-{congress}-{type}-{number}', e.g. 'US-119-HR-1968'"),
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), nullable=False, server_default="'US'"),
    Column("congress", Integer, nullable=False),
    Column("bill_type", String(10), nullable=False,
           comment="HR, S, HRES, SRES, HJRES, SJRES, HCONRES, SCONRES"),
    Column("bill_number", Integer, nullable=False),
    Column("origin_chamber", String(8), nullable=True),
    Column("title", Text, nullable=True),
    Column("short_title", String(300), nullable=True),
    Column("policy_area", String(100), nullable=True),
    Column("introduced_date", Date, nullable=True),
    Column("latest_action_date", Date, nullable=True),
    Column("latest_action_text", Text, nullable=True),
    Column("update_date", DateTime(timezone=True), nullable=True),
    Column("url", String(300), nullable=True),
    Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("country_code", "congress", "bill_type", "bill_number",
                     name="uq_bills_natural_key"),
    schema=SCHEMA,
)
Index("ix_bills_congress_action_date",
      bills.c.country_code, bills.c.congress, bills.c.latest_action_date.desc())
Index("ix_bills_policy_area", bills.c.policy_area)


# ---------------------------------------------------------------------------
# alt_political.bill_sponsors — M:N bills ↔ legislators with role
# ---------------------------------------------------------------------------
bill_sponsors = Table(
    "bill_sponsors",
    metadata,
    Column("bill_uid", String(40),
           ForeignKey(f"{SCHEMA}.bills.bill_uid", ondelete="CASCADE"),
           primary_key=True),
    Column("bioguide_id", String(7),
           ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
           primary_key=True),
    Column("role", String(10), primary_key=True,
           comment="'sponsor' | 'cosponsor'"),
    Column("sponsorship_date", Date, nullable=True),
    Column("withdrawn_date", Date, nullable=True),
    CheckConstraint("role IN ('sponsor','cosponsor')", name="bill_sponsors_role_valid"),
    schema=SCHEMA,
)
Index("ix_bill_sponsors_bioguide", bill_sponsors.c.bioguide_id)


# ---------------------------------------------------------------------------
# alt_political.bill_committees — bills referred to / acting committees
# ---------------------------------------------------------------------------
bill_committees = Table(
    "bill_committees",
    metadata,
    Column("bill_uid", String(40),
           ForeignKey(f"{SCHEMA}.bills.bill_uid", ondelete="CASCADE"),
           primary_key=True),
    Column("country_code", String(2), primary_key=True, server_default="'US'"),
    Column("committee_id", String(8), primary_key=True),
    Column("activity_date", Date, primary_key=True),
    Column("activity_type", String(60), primary_key=True,
           comment="'Referred to' | 'Markup by' | 'Reported by' | ..."),
    ForeignKeyConstraint(
        ["country_code", "committee_id"],
        [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
        name="fk_bill_committees_committee", ondelete="CASCADE",
    ),
    schema=SCHEMA,
)
Index("ix_bill_committees_committee_date",
      bill_committees.c.committee_id, bill_committees.c.activity_date.desc())


# ---------------------------------------------------------------------------
# alt_political.bill_actions — full action history per bill
# ---------------------------------------------------------------------------
bill_actions = Table(
    "bill_actions",
    metadata,
    Column("action_id", UUID(as_uuid=True), primary_key=True,
           server_default=text("gen_random_uuid()")),
    Column("bill_uid", String(40),
           ForeignKey(f"{SCHEMA}.bills.bill_uid", ondelete="CASCADE"),
           nullable=False),
    Column("action_date", Date, nullable=False),
    Column("action_text", Text, nullable=False),
    Column("action_type", String(60), nullable=True),
    Column("action_chamber", String(8), nullable=True),
    schema=SCHEMA,
)
Index("ix_bill_actions_bill_date",
      bill_actions.c.bill_uid, bill_actions.c.action_date.desc())


# ---------------------------------------------------------------------------
# alt_political.hearings — committee hearings
# ---------------------------------------------------------------------------
hearings = Table(
    "hearings",
    metadata,
    Column("country_code", String(2), ForeignKey(COUNTRY_FK), primary_key=True, server_default="'US'"),
    Column("jacket_number", Integer, primary_key=True),
    Column("congress", Integer, nullable=False),
    Column("chamber", String(8), nullable=False),
    Column("title", Text, nullable=True),
    Column("date_held", Date, nullable=True),
    Column("committee_country_code", String(2), nullable=True),
    Column("committee_id", String(8), nullable=True),
    Column("citation", String(200), nullable=True),
    Column("update_date", DateTime(timezone=True), nullable=True),
    Column("url", String(300), nullable=True),
    Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["committee_country_code", "committee_id"],
        [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
        name="fk_hearings_committee", ondelete="SET NULL",
    ),
    schema=SCHEMA,
)
Index("ix_hearings_committee_date",
      hearings.c.committee_id, hearings.c.date_held.desc())


# ---------------------------------------------------------------------------
# alt_political.hearing_witnesses — witnesses listed for each hearing
# ---------------------------------------------------------------------------
hearing_witnesses = Table(
    "hearing_witnesses",
    metadata,
    Column("country_code", String(2), primary_key=True, server_default="'US'"),
    Column("jacket_number", Integer, primary_key=True),
    Column("witness_seq", SmallInteger, primary_key=True),
    Column("name", String(200), nullable=False),
    Column("organization", String(300), nullable=True),
    Column("title", String(200), nullable=True),
    ForeignKeyConstraint(
        ["country_code", "jacket_number"],
        [f"{SCHEMA}.hearings.country_code", f"{SCHEMA}.hearings.jacket_number"],
        name="fk_hearing_witnesses_hearing", ondelete="CASCADE",
    ),
    schema=SCHEMA,
)


# ---------------------------------------------------------------------------
# alt_political.raw_archive — raw payload audit trail
# ---------------------------------------------------------------------------
raw_archive = Table(
    "raw_archive",
    metadata,
    Column("raw_id", UUID(as_uuid=True), primary_key=True,
           server_default=text("gen_random_uuid()")),
    Column("source", String(40), nullable=False,
           comment="'house_clerk_xml' | 'house_clerk_pdf' | 'senate_efd_html' | "
                   "'senate_efd_paper_viewer' | 'lda_filing' | 'usaspending_search' | "
                   "'finnhub_usa_spending' | 'fec_schedule_a' | 'congress_gov_bill' | "
                   "'congress_gov_hearing' | 'congress_gov_member' | "
                   "'sec_company_tickers' | 'unitedstates_yaml'"),
    Column("source_url", String(800), nullable=False),
    Column("response_bytes", LargeBinary, nullable=True,
           comment="Gzipped raw response body"),
    Column("response_headers", JSONB, nullable=True),
    Column("status_code", SmallInteger, nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("metadata_json", JSONB, nullable=True,
           comment="Source-specific metadata (filing_id, recipient_query, country_code, etc.)"),
    schema=SCHEMA,
)
Index("ix_raw_archive_source_fetched",
      raw_archive.c.source, raw_archive.c.fetched_at.desc())
Index("ix_raw_archive_url", raw_archive.c.source_url)
