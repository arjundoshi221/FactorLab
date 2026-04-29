"""Create alt_political schema — 26 tables for US Congress trades, lobbying,
contracts, donations, bills, hearings.

Revision ID: 007
Revises: 006
Create Date: 2026-04-30

Tables created in dependency-safe order:
  Independent dims    : legislators, asset_type_codes, lda_issue_codes,
                         lda_government_entities, fec_committees,
                         contract_aliases, lobby_client_aliases
  Dim with FK         : legislator_terms, legislator_fec_ids, committees,
                         committee_assignments, committee_sector_map
  Events              : legislator_trades, gov_contracts, lobbying_filings,
                         lobbying_activities, lobbying_activity_targets,
                         lobbying_activity_lobbyists, campaign_donations,
                         bills, bill_sponsors, bill_committees, bill_actions,
                         hearings, hearing_witnesses
  Audit               : raw_archive

Country-tagged: every dim + event has country_code FK to ref.countries.code.
v1 default 'US'; future jurisdictions reuse the schema.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "alt_political"
COUNTRY_FK = "ref.countries.code"
INSTRUMENT_FK = "ref.instruments.id"


def upgrade() -> None:
    # ======================================================================
    # Group A — independent dimensions
    # ======================================================================

    op.create_table(
        "legislators",
        sa.Column("bioguide_id", sa.String(7), primary_key=True),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), nullable=False, server_default="US"),
        sa.Column("first_name", sa.String(80), nullable=False),
        sa.Column("middle_name", sa.String(80), nullable=True),
        sa.Column("last_name", sa.String(80), nullable=False),
        sa.Column("suffix", sa.String(20), nullable=True),
        sa.Column("nickname", sa.String(80), nullable=True),
        sa.Column("official_full", sa.String(200), nullable=True),
        sa.Column("gender", sa.String(1), nullable=True),
        sa.Column("birthday", sa.Date, nullable=True),
        sa.Column("thomas_id", sa.String(8), nullable=True),
        sa.Column("govtrack_id", sa.Integer, nullable=True),
        sa.Column("opensecrets_cid", sa.String(20), nullable=True),
        sa.Column("wikipedia_id", sa.String(200), nullable=True),
        sa.Column("ballotpedia_id", sa.String(200), nullable=True),
        sa.Column("fec_candidate_id_primary", sa.String(9), nullable=True),
        sa.Column("in_office", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_legislators_country_last_first",
                    "legislators", ["country_code", "last_name", "first_name"], schema=SCHEMA)
    op.create_index("ix_legislators_in_office", "legislators", ["in_office"], schema=SCHEMA)

    op.create_table(
        "asset_type_codes",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("code", sa.String(2), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("equity_ticker_likely", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    op.create_table(
        "lda_issue_codes",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("code", sa.String(3), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    op.create_table(
        "lda_government_entities",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("entity_id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    op.create_table(
        "fec_committees",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("committee_id", sa.String(9), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("committee_type", sa.String(2), nullable=True),
        sa.Column("committee_type_full", sa.String(80), nullable=True),
        sa.Column("designation", sa.String(2), nullable=True),
        sa.Column("organization_type", sa.String(2), nullable=True),
        sa.Column("sponsor_company_ticker", sa.String(10), nullable=True),
        sa.Column("first_file_date", sa.Date, nullable=True),
        sa.Column("last_file_date", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_fec_committees_ticker", "fec_committees", ["sponsor_company_ticker"], schema=SCHEMA)
    op.create_index("ix_fec_committees_type", "fec_committees", ["committee_type"], schema=SCHEMA)

    op.create_table(
        "contract_aliases",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("normalized_name", sa.String(300), primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1.00"),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("first_seen_name", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "source IN ('manual','exact','prefix','fuzzy','learned','finnhub_xref')",
            name="contract_aliases_source_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_contract_aliases_ticker", "contract_aliases", ["ticker"], schema=SCHEMA)

    op.create_table(
        "lobby_client_aliases",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("normalized_name", sa.String(300), primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="1.00"),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "source IN ('manual','exact','prefix','fuzzy','learned')",
            name="lobby_client_aliases_source_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_lobby_client_aliases_ticker", "lobby_client_aliases", ["ticker"], schema=SCHEMA)

    # ======================================================================
    # Group B — dimensions with FK to A
    # ======================================================================

    op.create_table(
        "legislator_terms",
        sa.Column("bioguide_id", sa.String(7),
                  sa.ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("term_start", sa.Date, primary_key=True),
        sa.Column("term_end", sa.Date, nullable=False),
        sa.Column("chamber", sa.String(3), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("district", sa.Integer, nullable=True),
        sa.Column("party", sa.String(20), nullable=True),
        sa.Column("congress_number", sa.Integer, nullable=True),
        sa.CheckConstraint("chamber IN ('sen','rep')", name="legislator_terms_chamber_valid"),
        sa.CheckConstraint("term_end >= term_start", name="legislator_terms_end_after_start"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_legislator_terms_chamber_state", "legislator_terms", ["chamber", "state"], schema=SCHEMA)
    op.create_index("ix_legislator_terms_dates", "legislator_terms", ["term_start", "term_end"], schema=SCHEMA)

    op.create_table(
        "legislator_fec_ids",
        sa.Column("fec_candidate_id", sa.String(9), primary_key=True),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), nullable=False, server_default="US"),
        sa.Column("bioguide_id", sa.String(7),
                  sa.ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("office", sa.String(1), nullable=False),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("district", sa.Integer, nullable=True),
        sa.Column("first_election_year", sa.Integer, nullable=True),
        sa.CheckConstraint("office IN ('H','S','P')", name="legislator_fec_ids_office_valid"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_legislator_fec_ids_bioguide", "legislator_fec_ids", ["bioguide_id"], schema=SCHEMA)

    op.create_table(
        "committees",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("committee_id", sa.String(8), primary_key=True),
        sa.Column("chamber", sa.String(6), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("parent_country_code", sa.String(2), nullable=True),
        sa.Column("parent_committee_id", sa.String(8), nullable=True),
        sa.Column("jurisdiction", sa.Text, nullable=True),
        sa.Column("url", sa.String(300), nullable=True),
        sa.Column("rss_url", sa.String(300), nullable=True),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(
            ["parent_country_code", "parent_committee_id"],
            [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
            name="fk_committees_parent",
        ),
        sa.CheckConstraint("chamber IN ('senate','house','joint')", name="committees_chamber_valid"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_committees_chamber", "committees", ["chamber"], schema=SCHEMA)
    op.create_index("ix_committees_parent", "committees",
                    ["parent_country_code", "parent_committee_id"], schema=SCHEMA)

    op.create_table(
        "committee_assignments",
        sa.Column("country_code", sa.String(2), primary_key=True, server_default="US"),
        sa.Column("committee_id", sa.String(8), primary_key=True),
        sa.Column("bioguide_id", sa.String(7),
                  sa.ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("congress_number", sa.Integer, primary_key=True),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("rank", sa.Integer, nullable=True),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.ForeignKeyConstraint(
            ["country_code", "committee_id"],
            [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
            name="fk_committee_assignments_committee", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('chair','ranking_member','member','vice_chair','ex_officio',"
            "'chairwoman','cochairman','vice_chairman','vice_chairwoman')",
            name="committee_assignments_role_valid",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_committee_assignments_bioguide", "committee_assignments", ["bioguide_id"], schema=SCHEMA)
    op.create_index("ix_committee_assignments_dates",
                    "committee_assignments", ["valid_from", "valid_to"], schema=SCHEMA)

    op.create_table(
        "committee_sector_map",
        sa.Column("country_code", sa.String(2), primary_key=True, server_default="US"),
        sa.Column("committee_id", sa.String(8), primary_key=True),
        sa.Column("gics_code", sa.String(10), primary_key=True),
        sa.Column("signal_strength", sa.String(15), nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(
            ["country_code", "committee_id"],
            [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
            name="fk_committee_sector_map_committee", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "signal_strength IN ('extreme','very_high','high','medium_high','medium','low')",
            name="committee_sector_map_strength_valid",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    # ======================================================================
    # Group C — events
    # ======================================================================

    op.create_table(
        "legislator_trades",
        sa.Column("trade_id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), nullable=False, server_default="US"),
        sa.Column("chamber", sa.String(6), nullable=False),
        sa.Column("bioguide_id", sa.String(7),
                  sa.ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("legislator_name_raw", sa.String(200), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("filing_id", sa.String(50), nullable=False),
        sa.Column("filing_date", sa.Date, nullable=False),
        sa.Column("filing_url", sa.String(500), nullable=True),
        sa.Column("transaction_date", sa.Date, nullable=False),
        sa.Column("notification_date", sa.Date, nullable=True),
        sa.Column("filer_type", sa.String(20), nullable=False),
        sa.Column("transaction_type", sa.String(20), nullable=False),
        sa.Column("asset_name_raw", sa.Text, nullable=False),
        sa.Column("asset_type_code", sa.String(2), nullable=True),
        sa.Column("ticker", sa.String(10), nullable=True),
        sa.Column("security_id", UUID(as_uuid=True), sa.ForeignKey(INSTRUMENT_FK, ondelete="SET NULL"), nullable=True),
        sa.Column("amount_str", sa.String(40), nullable=False),
        sa.Column("amount_min", sa.BigInteger, nullable=True),
        sa.Column("amount_max", sa.BigInteger, nullable=True),
        sa.Column("amount_mid", sa.BigInteger, nullable=True),
        sa.Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("as_of_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["country_code", "asset_type_code"],
            [f"{SCHEMA}.asset_type_codes.country_code", f"{SCHEMA}.asset_type_codes.code"],
            name="fk_legislator_trades_asset_type",
        ),
        sa.UniqueConstraint(
            "country_code", "chamber", "filing_id", "transaction_date",
            "asset_name_raw", "transaction_type", "amount_str",
            name="uq_legislator_trades_dedup",
        ),
        sa.CheckConstraint("chamber IN ('senate','house')", name="legislator_trades_chamber_valid"),
        sa.CheckConstraint(
            "transaction_type IN ('purchase','sale_full','sale_partial','exchange')",
            name="legislator_trades_tx_type_valid",
        ),
        sa.CheckConstraint(
            "filer_type IN ('self','spouse','dependent_child','joint','junior')",
            name="legislator_trades_filer_type_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_legislator_trades_bioguide_date", "legislator_trades",
                    ["bioguide_id", "transaction_date"], schema=SCHEMA)
    op.create_index("ix_legislator_trades_ticker_date", "legislator_trades",
                    ["ticker", "transaction_date"], schema=SCHEMA)
    op.create_index("ix_legislator_trades_filing_date", "legislator_trades",
                    [sa.text("filing_date DESC")], schema=SCHEMA)
    op.create_index("ix_legislator_trades_country_filing", "legislator_trades",
                    ["country_code", "filing_id"], schema=SCHEMA)

    op.create_table(
        "gov_contracts",
        sa.Column("contract_id", sa.String(120), primary_key=True),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), nullable=False, server_default="US"),
        sa.Column("award_id", sa.String(60), nullable=True),
        sa.Column("recipient_legal_name", sa.String(300), nullable=False),
        sa.Column("recipient_parent_name", sa.String(300), nullable=True),
        sa.Column("recipient_uei", sa.String(12), nullable=True),
        sa.Column("recipient_id", sa.String(50), nullable=True),
        sa.Column("ticker", sa.String(10), nullable=True),
        sa.Column("security_id", UUID(as_uuid=True), sa.ForeignKey(INSTRUMENT_FK, ondelete="SET NULL"), nullable=True),
        sa.Column("total_value", sa.Numeric(20, 2), nullable=True),
        sa.Column("obligated_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("outlayed_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("potential_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("action_date", sa.Date, nullable=True),
        sa.Column("performance_start_date", sa.Date, nullable=True),
        sa.Column("performance_end_date", sa.Date, nullable=True),
        sa.Column("awarding_agency", sa.String(200), nullable=True),
        sa.Column("awarding_sub_agency", sa.String(200), nullable=True),
        sa.Column("awarding_office", sa.String(200), nullable=True),
        sa.Column("performance_state", sa.String(2), nullable=True),
        sa.Column("performance_country", sa.String(3), nullable=True),
        sa.Column("performance_district", sa.String(8), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("naics_code", sa.String(10), nullable=True),
        sa.Column("permalink", sa.String(500), nullable=True),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("source_recipient_query", sa.String(300), nullable=True),
        sa.Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_modified_date", sa.Date, nullable=True),
        sa.CheckConstraint(
            "source IN ('usaspending_direct','finnhub_usa_spending')",
            name="gov_contracts_source_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_gov_contracts_ticker_action_date", "gov_contracts",
                    ["ticker", sa.text("action_date DESC")], schema=SCHEMA)
    op.create_index("ix_gov_contracts_awarding_agency", "gov_contracts", ["awarding_agency"], schema=SCHEMA)
    op.create_index("ix_gov_contracts_naics", "gov_contracts", ["naics_code"], schema=SCHEMA)
    op.create_index("ix_gov_contracts_district", "gov_contracts", ["performance_district"], schema=SCHEMA)

    op.create_table(
        "lobbying_filings",
        sa.Column("filing_uuid", UUID(as_uuid=True), primary_key=True),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), nullable=False, server_default="US"),
        sa.Column("filing_type", sa.String(4), nullable=False),
        sa.Column("filing_year", sa.Integer, nullable=False),
        sa.Column("filing_period", sa.String(20), nullable=True),
        sa.Column("client_name", sa.String(300), nullable=False),
        sa.Column("client_id", sa.Integer, nullable=True),
        sa.Column("client_ticker", sa.String(10), nullable=True),
        sa.Column("registrant_name", sa.String(300), nullable=False),
        sa.Column("registrant_id", sa.Integer, nullable=True),
        sa.Column("income", sa.Numeric(15, 2), nullable=True),
        sa.Column("expenses", sa.Numeric(15, 2), nullable=True),
        sa.Column("dt_posted", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filing_document_url", sa.String(500), nullable=True),
        sa.Column("termination_date", sa.Date, nullable=True),
        sa.Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_lobbying_filings_client_ticker_year", "lobbying_filings",
                    ["client_ticker", "filing_year"], schema=SCHEMA)
    op.create_index("ix_lobbying_filings_client_name", "lobbying_filings", ["client_name"], schema=SCHEMA)
    op.create_index("ix_lobbying_filings_registrant_name", "lobbying_filings", ["registrant_name"], schema=SCHEMA)
    op.create_index("ix_lobbying_filings_year_period", "lobbying_filings",
                    ["filing_year", "filing_period"], schema=SCHEMA)

    op.create_table(
        "lobbying_activities",
        sa.Column("activity_id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("filing_uuid", UUID(as_uuid=True),
                  sa.ForeignKey(f"{SCHEMA}.lobbying_filings.filing_uuid", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="US"),
        sa.Column("issue_code", sa.String(3), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("foreign_entity_issues", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(
            ["country_code", "issue_code"],
            [f"{SCHEMA}.lda_issue_codes.country_code", f"{SCHEMA}.lda_issue_codes.code"],
            name="fk_lobbying_activities_issue",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_lobbying_activities_filing", "lobbying_activities", ["filing_uuid"], schema=SCHEMA)
    op.create_index("ix_lobbying_activities_issue_code", "lobbying_activities", ["issue_code"], schema=SCHEMA)

    op.create_table(
        "lobbying_activity_targets",
        sa.Column("activity_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{SCHEMA}.lobbying_activities.activity_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("country_code", sa.String(2), primary_key=True, server_default="US"),
        sa.Column("entity_id", sa.Integer, primary_key=True),
        sa.ForeignKeyConstraint(
            ["country_code", "entity_id"],
            [f"{SCHEMA}.lda_government_entities.country_code",
             f"{SCHEMA}.lda_government_entities.entity_id"],
            name="fk_lobbying_activity_targets_entity",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "lobbying_activity_lobbyists",
        sa.Column("activity_id", UUID(as_uuid=True),
                  sa.ForeignKey(f"{SCHEMA}.lobbying_activities.activity_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("lobbyist_id", sa.Integer, primary_key=True),
        sa.Column("last_name", sa.String(80), primary_key=True),
        sa.Column("first_name", sa.String(80), primary_key=True),
        sa.Column("middle_name", sa.String(80), nullable=True),
        sa.Column("suffix", sa.String(20), nullable=True),
        sa.Column("covered_position", sa.Text, nullable=True),
        sa.Column("is_new", sa.Boolean, nullable=False, server_default="false"),
        schema=SCHEMA,
    )
    op.create_index("ix_lobbying_activity_lobbyists_name", "lobbying_activity_lobbyists",
                    ["last_name", "first_name"], schema=SCHEMA)

    op.create_table(
        "campaign_donations",
        sa.Column("donation_id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), nullable=False, server_default="US"),
        sa.Column("sub_id", sa.String(50), nullable=True),
        sa.Column("cycle", sa.Integer, nullable=False),
        sa.Column("donor_name", sa.String(200), nullable=True),
        sa.Column("donor_employer", sa.String(200), nullable=True),
        sa.Column("donor_occupation", sa.String(200), nullable=True),
        sa.Column("donor_state", sa.String(2), nullable=True),
        sa.Column("donor_zip", sa.String(10), nullable=True),
        sa.Column("donor_city", sa.String(100), nullable=True),
        sa.Column("donor_committee_country", sa.String(2), nullable=True),
        sa.Column("donor_committee_id", sa.String(9), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("date", sa.Date, nullable=True),
        sa.Column("transaction_type", sa.String(10), nullable=True),
        sa.Column("recipient_committee_country", sa.String(2), nullable=True),
        sa.Column("recipient_committee_id", sa.String(9), nullable=True),
        sa.Column("recipient_committee_name", sa.String(300), nullable=True),
        sa.Column("candidate_id", sa.String(9),
                  sa.ForeignKey(f"{SCHEMA}.legislator_fec_ids.fec_candidate_id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("candidate_name", sa.String(200), nullable=True),
        sa.Column("filing_url", sa.String(500), nullable=True),
        sa.Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["donor_committee_country", "donor_committee_id"],
            [f"{SCHEMA}.fec_committees.country_code", f"{SCHEMA}.fec_committees.committee_id"],
            name="fk_campaign_donations_donor_committee", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_committee_country", "recipient_committee_id"],
            [f"{SCHEMA}.fec_committees.country_code", f"{SCHEMA}.fec_committees.committee_id"],
            name="fk_campaign_donations_recipient_committee", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("sub_id", name="uq_campaign_donations_sub_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_campaign_donations_recipient_date", "campaign_donations",
                    ["recipient_committee_id", sa.text("date DESC")], schema=SCHEMA)
    op.create_index("ix_campaign_donations_donor_employer_cycle", "campaign_donations",
                    ["donor_employer", "cycle"], schema=SCHEMA)
    op.create_index("ix_campaign_donations_donor_committee", "campaign_donations",
                    ["donor_committee_id"], schema=SCHEMA)
    op.create_index("ix_campaign_donations_candidate", "campaign_donations", ["candidate_id"], schema=SCHEMA)

    op.create_table(
        "bills",
        sa.Column("bill_uid", sa.String(40), primary_key=True),
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), nullable=False, server_default="US"),
        sa.Column("congress", sa.Integer, nullable=False),
        sa.Column("bill_type", sa.String(10), nullable=False),
        sa.Column("bill_number", sa.Integer, nullable=False),
        sa.Column("origin_chamber", sa.String(8), nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("short_title", sa.String(300), nullable=True),
        sa.Column("policy_area", sa.String(100), nullable=True),
        sa.Column("introduced_date", sa.Date, nullable=True),
        sa.Column("latest_action_date", sa.Date, nullable=True),
        sa.Column("latest_action_text", sa.Text, nullable=True),
        sa.Column("update_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("url", sa.String(300), nullable=True),
        sa.Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("country_code", "congress", "bill_type", "bill_number", name="uq_bills_natural_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_bills_congress_action_date", "bills",
                    ["country_code", "congress", sa.text("latest_action_date DESC")], schema=SCHEMA)
    op.create_index("ix_bills_policy_area", "bills", ["policy_area"], schema=SCHEMA)

    op.create_table(
        "bill_sponsors",
        sa.Column("bill_uid", sa.String(40),
                  sa.ForeignKey(f"{SCHEMA}.bills.bill_uid", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("bioguide_id", sa.String(7),
                  sa.ForeignKey(f"{SCHEMA}.legislators.bioguide_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("role", sa.String(10), primary_key=True),
        sa.Column("sponsorship_date", sa.Date, nullable=True),
        sa.Column("withdrawn_date", sa.Date, nullable=True),
        sa.CheckConstraint("role IN ('sponsor','cosponsor')", name="bill_sponsors_role_valid"),
        schema=SCHEMA,
    )
    op.create_index("ix_bill_sponsors_bioguide", "bill_sponsors", ["bioguide_id"], schema=SCHEMA)

    op.create_table(
        "bill_committees",
        sa.Column("bill_uid", sa.String(40),
                  sa.ForeignKey(f"{SCHEMA}.bills.bill_uid", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("country_code", sa.String(2), primary_key=True, server_default="US"),
        sa.Column("committee_id", sa.String(8), primary_key=True),
        sa.Column("activity_date", sa.Date, primary_key=True),
        sa.Column("activity_type", sa.String(60), primary_key=True),
        sa.ForeignKeyConstraint(
            ["country_code", "committee_id"],
            [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
            name="fk_bill_committees_committee", ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_bill_committees_committee_date", "bill_committees",
                    ["committee_id", sa.text("activity_date DESC")], schema=SCHEMA)

    op.create_table(
        "bill_actions",
        sa.Column("action_id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bill_uid", sa.String(40),
                  sa.ForeignKey(f"{SCHEMA}.bills.bill_uid", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("action_date", sa.Date, nullable=False),
        sa.Column("action_text", sa.Text, nullable=False),
        sa.Column("action_type", sa.String(60), nullable=True),
        sa.Column("action_chamber", sa.String(8), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_bill_actions_bill_date", "bill_actions",
                    ["bill_uid", sa.text("action_date DESC")], schema=SCHEMA)

    op.create_table(
        "hearings",
        sa.Column("country_code", sa.String(2), sa.ForeignKey(COUNTRY_FK), primary_key=True, server_default="US"),
        sa.Column("jacket_number", sa.Integer, primary_key=True),
        sa.Column("congress", sa.Integer, nullable=False),
        sa.Column("chamber", sa.String(8), nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("date_held", sa.Date, nullable=True),
        sa.Column("committee_country_code", sa.String(2), nullable=True),
        sa.Column("committee_id", sa.String(8), nullable=True),
        sa.Column("citation", sa.String(200), nullable=True),
        sa.Column("update_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("url", sa.String(300), nullable=True),
        sa.Column("raw_archive_id", UUID(as_uuid=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["committee_country_code", "committee_id"],
            [f"{SCHEMA}.committees.country_code", f"{SCHEMA}.committees.committee_id"],
            name="fk_hearings_committee", ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_hearings_committee_date", "hearings",
                    ["committee_id", sa.text("date_held DESC")], schema=SCHEMA)

    op.create_table(
        "hearing_witnesses",
        sa.Column("country_code", sa.String(2), primary_key=True, server_default="US"),
        sa.Column("jacket_number", sa.Integer, primary_key=True),
        sa.Column("witness_seq", sa.SmallInteger, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("organization", sa.String(300), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["country_code", "jacket_number"],
            [f"{SCHEMA}.hearings.country_code", f"{SCHEMA}.hearings.jacket_number"],
            name="fk_hearing_witnesses_hearing", ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "raw_archive",
        sa.Column("raw_id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.String(800), nullable=False),
        sa.Column("response_bytes", sa.LargeBinary, nullable=True),
        sa.Column("response_headers", JSONB, nullable=True),
        sa.Column("status_code", sa.SmallInteger, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata_json", JSONB, nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_raw_archive_source_fetched", "raw_archive",
                    ["source", sa.text("fetched_at DESC")], schema=SCHEMA)
    op.create_index("ix_raw_archive_url", "raw_archive", ["source_url"], schema=SCHEMA)


def downgrade() -> None:
    """Drop in reverse dependency order."""
    for tbl in [
        "raw_archive",
        "hearing_witnesses", "hearings",
        "bill_actions", "bill_committees", "bill_sponsors", "bills",
        "campaign_donations",
        "lobbying_activity_lobbyists", "lobbying_activity_targets", "lobbying_activities",
        "lobbying_filings",
        "gov_contracts",
        "legislator_trades",
        "committee_sector_map", "committee_assignments", "committees",
        "legislator_fec_ids", "legislator_terms",
        "lobby_client_aliases", "contract_aliases", "fec_committees",
        "lda_government_entities", "lda_issue_codes", "asset_type_codes",
        "legislators",
    ]:
        op.drop_table(tbl, schema=SCHEMA)
